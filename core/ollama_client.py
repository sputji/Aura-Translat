from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import ollama


logger = logging.getLogger(__name__)


@dataclass
class OllamaConfig:
    host: str
    model: str
    system_prompt: str


class OllamaTranslator:
    _autostart_lock = threading.Lock()

    def __init__(self, config: OllamaConfig) -> None:
        self.config = config
        # Keep SDK client for discovery/start checks.
        self.client = ollama.Client(host=config.host, timeout=6.0)

    def ensure_service(self) -> None:
        if self.ensure_service_with_autostart(self.config.host, startup_timeout=6.0):
            logger.info("Service Ollama deja actif sur le port 11434.")
            return

        raise RuntimeError(
            "Service Ollama non detecte sur 127.0.0.1:11434. "
            "Demarre Ollama manuellement puis relance la capture."
        )

    @classmethod
    def ensure_service_with_autostart(cls, host: str, startup_timeout: float = 6.0) -> bool:
        parsed = urlparse((host or "").strip())
        hostname = (parsed.hostname or "").strip().lower()
        if hostname not in {"", "127.0.0.1", "localhost"}:
            return False

        if cls._is_port_open("127.0.0.1", 11434):
            return True

        started = cls._start_ollama_silently()
        if not started:
            return False

        deadline = time.monotonic() + max(0.8, float(startup_timeout))
        while time.monotonic() < deadline:
            if cls._is_port_open("127.0.0.1", 11434):
                return True
            time.sleep(0.25)
        return cls._is_port_open("127.0.0.1", 11434)

    @classmethod
    def _start_ollama_silently(cls) -> bool:
        with cls._autostart_lock:
            if cls._is_port_open("127.0.0.1", 11434):
                return True

            ollama_bin = shutil.which("ollama")
            if not ollama_bin:
                logger.debug("Binaire ollama introuvable dans PATH.")
                return False

            kwargs: dict[str, Any] = {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "close_fds": True,
            }
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

            try:
                subprocess.Popen([ollama_bin, "serve"], **kwargs)  # noqa: S603,S607
                logger.info("Demarrage silencieux Ollama tente (ollama serve).")
                return True
            except Exception:  # pylint: disable=broad-exception-caught
                logger.debug("Echec demarrage silencieux Ollama.", exc_info=True)
                return False

    def translate(self, text: str, source_language: str, target_language: str) -> str:
        source_text = text.strip()
        if not source_text:
            return ""

        if self._should_passthrough_source(source_text, source_language, target_language):
            return source_text

        user_prompt = (
            "Règles obligatoires:\n"
            f"1) Traduis exactement vers {target_language}.\n"
            "2) N'ajoute aucun commentaire, aucune justification, aucune note de sécurité.\n"
            "3) Si le texte est déjà dans la langue cible, renvoie le texte identique.\n"
            "4) Si un fragment est incomplet, traduis seulement ce qui est certain, sans inventer.\n"
            f"Texte source ({source_language}) :\n"
            f"{source_text}"
        )

        try:
            translated = self._translate_http(model=self.config.model, user_prompt=user_prompt)
            cleaned = self._sanitize_translation_output(source_text, translated, target_language)
            if self._is_target_mismatch(cleaned, source_text, source_language, target_language):
                # Retry once with stricter constraints when the model keeps outputting source language.
                strict_prompt = (
                    "TRADUCTION OBLIGATOIRE.\n"
                    f"Langue source: {source_language}\n"
                    f"Langue cible: {target_language}\n"
                    "Reponds uniquement dans la langue cible, jamais dans la langue source.\n"
                    "Ne donne aucune explication.\n"
                    f"Texte a traduire:\n{source_text}"
                )
                strict_translated = self._translate_http(model=self.config.model, user_prompt=strict_prompt)
                strict_cleaned = self._sanitize_translation_output(source_text, strict_translated, target_language)
                if not self._is_target_mismatch(strict_cleaned, source_text, source_language, target_language):
                    return strict_cleaned
                return ""
            return cleaned
        except Exception as exc:  # pylint: disable=broad-exception-caught
            message = str(exc).lower()
            if "not found" not in message and "404" not in message:
                logger.debug("Traduction HTTP en erreur: %s", exc, exc_info=True)
                return ""

            # If the selected model was removed/renamed, auto-fallback to a live-capable model.
            available = self.discover_local_model_names(self.config.host)
            fallback_model = self.pick_live_translation_model(available, preferred="")
            if not fallback_model or fallback_model == self.config.model:
                logger.debug("Aucun modele fallback disponible apres erreur model not found.")
                return ""

            logger.warning(
                "Modele traduction indisponible (%s). Fallback auto vers %s.",
                self.config.model,
                fallback_model,
            )
            self.config.model = fallback_model
            try:
                translated = self._translate_http(model=self.config.model, user_prompt=user_prompt)
                cleaned = self._sanitize_translation_output(source_text, translated, target_language)
                if self._is_target_mismatch(cleaned, source_text, source_language, target_language):
                    return ""
                return cleaned
            except Exception as fallback_exc:  # pylint: disable=broad-exception-caught
                logger.debug("Traduction fallback en erreur: %s", fallback_exc, exc_info=True)
                return ""

    def _translate_http(self, model: str, user_prompt: str) -> str:
        payload = {
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": self.config.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "options": {"temperature": 0.0},
        }

        timeout = httpx.Timeout(connect=2.5, read=18.0, write=8.0, pool=2.5)
        endpoint = self.config.host.rstrip("/") + "/api/chat"
        with httpx.Client(timeout=timeout) as client:
            response = client.post(endpoint, json=payload)
            response.raise_for_status()
            data = response.json()

        if not isinstance(data, dict):
            return ""
        message = data.get("message") if isinstance(data, dict) else None
        if isinstance(message, dict):
            return str(message.get("content", "")).strip()
        return ""

    @staticmethod
    def _sanitize_translation_output(source_text: str, translated_text: str, target_language: str) -> str:
        cleaned = translated_text.strip()
        if not cleaned:
            return ""

        lowered = cleaned.lower()
        forbidden_meta = [
            "je ne peux pas",
            "i can't",
            "i cannot",
            "i'm sorry",
            "désolé",
            "en tant qu",
            "réponse traduite",
            "texte source",
            "voici",
        ]
        if any(token in lowered for token in forbidden_meta):
            return source_text

        # Remove common wrapper prefixes that some models prepend.
        for prefix in [
            "traduction:",
            "translation:",
            "traducción:",
            "übersetzung:",
            "traduzione:",
            "réponse traduite en français :",
            "réponse traduite :",
        ]:
            if lowered.startswith(prefix):
                cleaned = cleaned[len(prefix) :].strip(" \n:\t")
                lowered = cleaned.lower()

        if not cleaned:
            return ""

        # Guard against long hallucinated monologues.
        if len(cleaned) > max(450, len(source_text) * 3 + 40):
            return source_text

        if cleaned.count("\n") > 3:
            return source_text

        # If source already looks like target language, avoid needless rewriting.
        detected_source = OllamaTranslator._detect_language_from_markers(source_text)
        target_key = OllamaTranslator._normalize_language_key(target_language)
        if target_key and detected_source == target_key:
            return source_text

        return cleaned

    @staticmethod
    def _looks_like_french(text: str) -> bool:
        lowered = text.lower()
        french_markers = [
            " le ",
            " la ",
            " les ",
            " des ",
            " une ",
            " un ",
            " est ",
            " et ",
            " vous ",
            " je ",
            " nous ",
            " ils ",
            " elles ",
        ]
        accent_count = sum(1 for ch in text if ch in "àâäçéèêëîïôöùûüÿœ")
        marker_hits = sum(1 for marker in french_markers if marker in f" {lowered} ")
        return accent_count >= 1 or marker_hits >= 2

    @staticmethod
    def _should_passthrough_source(source_text: str, source_language: str, target_language: str) -> bool:
        source_key = OllamaTranslator._normalize_language_key(source_language)
        target_key = OllamaTranslator._normalize_language_key(target_language)
        if not source_key or not target_key:
            return False
        if source_key == target_key:
            return True

        # If user selected a target and source text already appears in this target, passthrough.
        detected_source = OllamaTranslator._detect_language_from_markers(source_text)
        return detected_source == target_key

    @staticmethod
    def _looks_like_english(text: str) -> bool:
        lowered = f" {text.lower()} "
        markers = [
            " the ",
            " and ",
            " to ",
            " of ",
            " in ",
            " for ",
            " with ",
            " is ",
            " are ",
        ]
        score = sum(1 for marker in markers if marker in lowered)
        return score >= 2

    @classmethod
    def _is_target_mismatch(
        cls,
        translated_text: str,
        source_text: str,
        source_language: str,
        target_language: str,
    ) -> bool:
        cleaned = (translated_text or "").strip()
        if not cleaned:
            return False

        source_key = cls._normalize_language_key(source_language)
        target_key = cls._normalize_language_key(target_language)
        if not target_key or not source_key or source_key == target_key:
            return False

        translated_key = cls._detect_language_from_markers(cleaned)
        source_detected = cls._detect_language_from_markers(source_text)
        source_ref = source_detected or source_key

        # If translated text stays in source language and not in target language, reject it.
        if translated_key and translated_key == source_ref and translated_key != target_key:
            return True

        # Same text as source across different source/target is likely untranslated output.
        if cleaned.lower() == source_text.strip().lower():
            return True

        return False

    @staticmethod
    def _normalize_language_key(label: str) -> str:
        value = (label or "").strip().lower()
        if value.startswith("en") or "english" in value:
            return "en"
        if value.startswith("fr") or "fran" in value:
            return "fr"
        if value.startswith("es") or "span" in value:
            return "es"
        if value.startswith("de") or "german" in value or "allem" in value:
            return "de"
        if value.startswith("it") or "ital" in value:
            return "it"
        return ""

    @classmethod
    def _detect_language_from_markers(cls, text: str) -> str:
        lowered = f" {(text or '').lower()} "
        markers = {
            "en": [" the ", " and ", " is ", " are ", " with ", " to ", " of "],
            "fr": [" le ", " la ", " les ", " des ", " est ", " avec ", " pour ", " je ", " vous "],
            "es": [" el ", " la ", " los ", " las ", " de ", " que ", " y ", " para ", " con "],
            "de": [" der ", " die ", " das ", " und ", " ist ", " mit ", " nicht ", " für "],
            "it": [" il ", " lo ", " gli ", " la ", " le ", " e ", " con ", " per ", " che "],
        }

        scores: dict[str, int] = dict.fromkeys(markers, 0)
        for lang_key, lang_markers in markers.items():
            for marker in lang_markers:
                if marker in lowered:
                    scores[lang_key] += 1

        best_lang = max(scores, key=scores.get)
        return best_lang if scores[best_lang] >= 2 else ""

    @staticmethod
    def pick_live_translation_model(model_names: list[str], preferred: str = "") -> str:
        if not model_names:
            return preferred.strip()

        lower_map = {name.lower(): name for name in model_names}
        preferred_clean = preferred.strip().lower()
        if preferred_clean and preferred_clean in lower_map:
            return lower_map[preferred_clean]

        priorities = [
            "llama3.1:8b",
            "qwen2.5:7b",
            "llama3.2:3b",
            "mistral-nemo:latest",
            "mistral:latest",
            "qwen3.5:9b",
        ]
        for wanted in priorities:
            if wanted in lower_map:
                return lower_map[wanted]

        for name in model_names:
            lowered = name.lower()
            if "coder" in lowered or "deepseek-r1" in lowered or "code" in lowered:
                continue
            return name

        return model_names[0]

    @staticmethod
    def recommend_translation_models(
        installed_model_names: list[str],
        source_language: str,
        target_language: str,
    ) -> dict[str, str]:
        installed = [name.strip() for name in installed_model_names if name and name.strip()]
        installed_lower = {name.lower(): name for name in installed}

        live_priority = OllamaTranslator._recommended_live_candidates(source_language, target_language)
        file_priority = OllamaTranslator._recommended_file_candidates(source_language, target_language)

        live_selected = ""
        for candidate in live_priority:
            if candidate.lower() in installed_lower:
                live_selected = installed_lower[candidate.lower()]
                break

        if not live_selected and installed:
            ranked = sorted(installed, key=lambda name: OllamaTranslator._score_live_model(name, source_language, target_language))
            live_selected = ranked[0]

        file_selected = ""
        for candidate in file_priority:
            if candidate.lower() in installed_lower:
                file_selected = installed_lower[candidate.lower()]
                break

        if not file_selected and installed:
            ranked = sorted(installed, key=lambda name: OllamaTranslator._score_file_model(name, source_language, target_language))
            file_selected = ranked[0]

        if live_selected:
            live_missing_suggestion = ""
        elif live_priority:
            live_missing_suggestion = live_priority[0]
        else:
            live_missing_suggestion = ""

        if file_selected:
            file_missing_suggestion = ""
        elif file_priority:
            file_missing_suggestion = file_priority[0]
        else:
            file_missing_suggestion = ""

        return {
            "live_recommended": live_selected,
            "file_recommended": file_selected,
            "live_missing_suggestion": live_missing_suggestion,
            "file_missing_suggestion": file_missing_suggestion,
        }

    @staticmethod
    def _recommended_live_candidates(source_language: str, target_language: str) -> list[str]:
        _ = source_language, target_language
        # Fast multilingual-first list for live captioning.
        return [
            "phi3:mini",
            "gemma2:2b",
            "llama3.2:3b",
            "qwen2.5:3b",
            "qwen2.5:7b",
            "llama3.1:8b",
            "mistral:latest",
        ]

    @staticmethod
    def _recommended_file_candidates(source_language: str, target_language: str) -> list[str]:
        _ = source_language, target_language
        # Better quality/faithfulness list for offline media translation.
        return [
            "qwen2.5:7b",
            "llama3.1:8b",
            "qwen3.5:9b",
            "mistral:latest",
            "gemma2:9b",
            "mixtral:8x7b",
            "gemma2:2b",
            "phi3:mini",
        ]

    @staticmethod
    def _score_live_model(name: str, source_language: str, target_language: str) -> tuple[int, int, int, str]:
        lowered = name.lower()
        size_b = OllamaTranslator._extract_model_size_billions(lowered)

        family_penalty = 0
        if "deepseek-r1" in lowered or "coder" in lowered or "code" in lowered:
            family_penalty += 12
        if "phi3" in lowered or "gemma2:2b" in lowered or "llama3.2:3b" in lowered:
            family_penalty -= 5

        multilingual_bonus = 0
        if any(tag in lowered for tag in ["qwen", "llama", "mistral", "gemma", "phi"]):
            multilingual_bonus -= 2

        # Prefer small models for live latency.
        size_penalty = int(size_b * 2)
        if size_b <= 3.2:
            size_penalty -= 3

        lang_pair_penalty = 0
        src = source_language.strip().lower()
        tgt = target_language.strip().lower()
        if src != tgt and any(tag in lowered for tag in ["qwen", "llama", "mistral", "gemma", "phi"]):
            lang_pair_penalty -= 1

        return (family_penalty + size_penalty + multilingual_bonus + lang_pair_penalty, int(size_b * 10), len(lowered), lowered)

    @staticmethod
    def _score_file_model(name: str, source_language: str, target_language: str) -> tuple[int, int, int, str]:
        lowered = name.lower()
        size_b = OllamaTranslator._extract_model_size_billions(lowered)

        family_penalty = 0
        if "deepseek-r1" in lowered or "coder" in lowered or "code" in lowered:
            family_penalty += 16

        quality_penalty = 0
        # Prefer medium models (7b-10b) for better quality/speed balance on files.
        if size_b < 6.5:
            quality_penalty += 5
        elif size_b > 12.5:
            quality_penalty += 4
        else:
            quality_penalty -= 3

        if any(tag in lowered for tag in ["qwen2.5:7b", "llama3.1:8b", "qwen3.5:9b", "mistral:latest"]):
            quality_penalty -= 4

        src = source_language.strip().lower()
        tgt = target_language.strip().lower()
        multilingual_bonus = -1 if src != tgt and any(tag in lowered for tag in ["qwen", "llama", "mistral", "gemma", "phi"]) else 0

        return (family_penalty + quality_penalty + multilingual_bonus, abs(int(size_b * 10) - 80), len(lowered), lowered)

    @staticmethod
    def _extract_model_size_billions(model_name_lower: str) -> float:
        tokens = model_name_lower.replace("-", ":").split(":")
        for token in tokens:
            if token.endswith("b") and token[:-1].replace(".", "", 1).isdigit():
                try:
                    return float(token[:-1])
                except Exception:
                    continue
        if "mini" in model_name_lower:
            return 2.0
        return 8.0

    @staticmethod
    def install_model(model_name: str) -> tuple[bool, str]:
        cleaned = (model_name or "").strip()
        if not cleaned:
            return False, "Nom de modele vide"

        ollama_bin = shutil.which("ollama")
        if not ollama_bin:
            return False, "Commande ollama introuvable dans le PATH"

        try:
            result = subprocess.run(
                [ollama_bin, "pull", cleaned],
                capture_output=True,
                text=True,
                timeout=1800,
                check=False,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return False, str(exc)

        output = (result.stdout or "") + "\n" + (result.stderr or "")
        if result.returncode == 0:
            return True, output.strip()[:800]
        return False, output.strip()[:800]

    @staticmethod
    def discover_local_model_names(host: str | None = None) -> list[str]:
        names: list[str] = []

        if host is not None:
            try:
                # Use a short timeout to avoid blocking the settings UI.
                response = ollama.Client(host=host, timeout=2.5).list()
                names.extend(OllamaTranslator._extract_model_names(response))
            except Exception:  # pylint: disable=broad-exception-caught
                logger.debug("Ollama API list() failed during discovery.", exc_info=True)

        names.extend(OllamaTranslator._scan_model_names_from_filesystem())

        deduped: list[str] = []
        seen: set[str] = set()
        for name in names:
            cleaned = name.strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                deduped.append(cleaned)
        return sorted(deduped, key=str.lower)

    @staticmethod
    def _extract_content(response: Any) -> str:
        if hasattr(response, "message"):
            message = getattr(response, "message")
            if isinstance(message, dict):
                return str(message.get("content", ""))
            return str(getattr(message, "content", ""))

        if isinstance(response, dict):
            return str(response.get("message", {}).get("content", ""))

        return ""

    @staticmethod
    def _extract_model_names(response: Any) -> list[str]:
        models = []
        if hasattr(response, "models"):
            models = list(getattr(response, "models") or [])
        elif isinstance(response, dict):
            models = list(response.get("models", []))

        names: list[str] = []
        for model in models:
            if isinstance(model, dict):
                name = model.get("name") or model.get("model")
            else:
                name = getattr(model, "model", None) or getattr(model, "name", None)
            if name:
                names.append(str(name))
        return names

    @staticmethod
    def _scan_model_names_from_filesystem() -> list[str]:
        roots: list[Path] = []

        env_root = os.environ.get("OLLAMA_MODELS")
        if env_root:
            roots.append(Path(env_root))

        home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
        if home:
            roots.append(Path(home) / ".ollama" / "models")
            roots.append(Path(home) / "AppData" / "Local" / "Ollama" / "models")
            roots.append(Path(home) / "AppData" / "Roaming" / "Ollama" / "models")

        # Avoid scanning whole drives recursively: this can freeze the settings window.

        names: list[str] = []
        for root in roots:
            if not root.exists():
                continue

            for manifest in root.rglob("*.json"):
                parts = [part.lower() for part in manifest.parts]
                if "manifests" not in parts and "models" not in parts:
                    continue

                stem = manifest.stem
                if stem and len(stem) > 8:
                    names.append(stem)

        return names

    @staticmethod
    def _is_port_open(host: str, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            return sock.connect_ex((host, port)) == 0

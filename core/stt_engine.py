from __future__ import annotations

import logging
import shutil
import ctypes
from dataclasses import dataclass
from collections.abc import Callable
from pathlib import Path
from typing import Iterable

import httpx
import numpy as np
from faster_whisper import WhisperModel
from huggingface_hub import hf_hub_url


logger = logging.getLogger(__name__)


@dataclass
class TimedSegment:
    start: float
    end: float
    text: str


class FasterWhisperEngine:
    _REQUIRED_MODEL_FILES = (
        "config.json",
        "tokenizer.json",
        "vocabulary.txt",
        "model.bin",
    )

    def __init__(
        self,
        model_size: str = "base",
        device: str = "auto",
        compute_type: str = "int8",
        language: str = "en",
        cache_dir: str | None = None,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        self.language = language
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.cache_dir = Path(cache_dir).expanduser() if cache_dir else None
        self._on_status = on_status
        self._model_ref: str | None = None
        self._cpu_fallback_applied = False
        self._active_model_size = model_size
        self._auto_forced_cpu = False
        self._preflight_force_cpu_if_needed()
        logger.info(
            "Chargement Whisper model=%s, device=%s, compute_type=%s",
            model_size,
            device,
            compute_type,
        )
        self.model = self._load_model_with_repair()

    def transcribe(self, samples: np.ndarray) -> str:
        if samples.size == 0:
            return ""

        samples = self._prepare_samples(samples)

        effective_language = self._effective_language()

        try:
            text = self._transcribe_once(samples, language=effective_language, vad_filter=False)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            if not self._should_fallback_to_cpu(exc):
                raise

            self._notify_status("CUDA indisponible. Bascule automatique STT sur CPU...")
            logger.warning("CUDA indisponible pour faster-whisper. Fallback CPU: %s", exc)
            self.model = self._build_model_for_cpu()
            self._cpu_fallback_applied = True
            text = self._transcribe_once(samples, language=effective_language, vad_filter=False)

        if text.strip():
            return text.strip()

        # Live fallback 1: activate VAD to reduce noisy non-speech chunks.
        text = self._transcribe_once(samples, language=effective_language, vad_filter=True)
        if text.strip():
            return text.strip()

        # Live fallback 2: allow auto language detection when configured language is too strict.
        if effective_language:
            text = self._transcribe_once(samples, language=None, vad_filter=True)

        return text.strip()

    def transcribe_relaxed(self, samples: np.ndarray) -> str:
        if samples.size == 0:
            return ""

        samples = self._prepare_samples(samples)
        try:
            segments, _ = self.model.transcribe(
                samples,
                language=None,
                beam_size=2,
                best_of=2,
                temperature=0.0,
                vad_filter=False,
            )
            return self._join_segments(segments).strip()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug("STT transcribe_relaxed failed: %s", exc, exc_info=True)
            return ""

    def transcribe_file_segments(
        self,
        media_path: str | Path,
        on_status: Callable[[str], None] | None = None,
    ) -> list[str]:
        path = Path(media_path)
        if not path.exists():
            raise FileNotFoundError(f"Fichier media introuvable: {path}")

        logger.info("Transcription fichier media: %s", path)
        if on_status is not None:
            on_status("Analyse audio du fichier en cours...")

        segments, _ = self.model.transcribe(
            str(path),
            language=self._effective_language(),
            beam_size=2,
            best_of=2,
            temperature=0.0,
            vad_filter=True,
        )

        collected: list[str] = []
        for segment in segments:
            text = (segment.text or "").strip()
            if text:
                collected.append(text)

        logger.info("Transcription fichier terminee: %s segments", len(collected))
        if on_status is not None:
            on_status(f"Transcription terminee: {len(collected)} segments detectes.")

        return collected

    def transcribe_file_timed_segments(
        self,
        media_path: str | Path,
        on_status: Callable[[str], None] | None = None,
        on_progress: Callable[[float, str], None] | None = None,
    ) -> list[TimedSegment]:
        path = Path(media_path)
        if not path.exists():
            raise FileNotFoundError(f"Fichier media introuvable: {path}")

        logger.info("Transcription temporelle fichier media: %s", path)
        if on_status is not None:
            on_status("Analyse audio temporelle du fichier en cours...")

        segments, info = self.model.transcribe(
            str(path),
            language=self._effective_language(),
            beam_size=2,
            best_of=2,
            temperature=0.0,
            vad_filter=True,
        )
        total_duration = float(getattr(info, "duration", 0.0) or 0.0)

        timed: list[TimedSegment] = []
        for index, segment in enumerate(segments, start=1):
            text = (segment.text or "").strip()
            if not text:
                continue
            start_value = float(getattr(segment, "start", 0.0) or 0.0)
            end_value = float(getattr(segment, "end", 0.0) or 0.0)
            timed.append(
                TimedSegment(
                    start=start_value,
                    end=end_value,
                    text=text,
                )
            )
            if on_progress is not None:
                if total_duration > 0.0 and end_value > 0.0:
                    ratio = max(0.0, min(1.0, end_value / total_duration))
                else:
                    ratio = min(0.98, index / 150.0)
                on_progress(ratio, f"Analyse audio: segment {index} detecte")

        logger.info("Transcription temporelle terminee: %s segments", len(timed))
        if on_status is not None:
            on_status(f"Transcription temporelle terminee: {len(timed)} segments detectes.")

        return timed

    def _transcribe_once(self, samples: np.ndarray, language: str | None, vad_filter: bool) -> str:
        segments, _ = self.model.transcribe(
            samples,
            language=language,
            beam_size=1,
            best_of=1,
            temperature=0.0,
            vad_filter=vad_filter,
        )
        return self._join_segments(segments)

    @staticmethod
    def _join_segments(segments: Iterable) -> str:
        return " ".join(segment.text.strip() for segment in segments if segment.text and segment.text.strip())

    def _effective_language(self) -> str | None:
        language = (self.language or "").strip().lower()
        if not language or language == "auto":
            return None
        return language

    def _load_model_with_repair(self) -> WhisperModel:
        try:
            return self._build_model_once()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            if not self._is_model_file_error(exc):
                raise

            self._notify_status("Cache STT local invalide detecte. Reparation automatique en cours...")
            logger.warning("Echec ouverture model.bin. Tentative de reparation cache Whisper: %s", exc)
            self._purge_model_cache()

            try:
                self._notify_status("Reinstallation du modele STT local...")
                return self._build_model_once()
            except Exception as retry_exc:  # pylint: disable=broad-exception-caught
                logger.exception("Echec apres reparation automatique du cache Whisper.")
                if self.model_size != "tiny":
                    self._notify_status(
                        "Le modele STT selectionne est indisponible. Bascule automatique vers tiny..."
                    )
                    logger.warning(
                        "Fallback STT vers tiny apres echec modele '%s': %s",
                        self.model_size,
                        retry_exc,
                    )
                    self.model_size = "tiny"
                    self._cpu_fallback_applied = False
                    self._purge_model_cache()
                    return self._build_model_once()

                raise RuntimeError(
                    "Impossible de charger le modele STT local (cache corrompu meme apres reparation). "
                    "Verifier la connexion Internet pour le premier lancement puis relancer l'application."
                ) from retry_exc

    def _build_model_once(self) -> WhisperModel:
        model_ref = self._resolve_model_reference()
        self._model_ref = model_ref
        self._active_model_size = self.model_size
        logger.info("WhisperModel ref: %s", model_ref)
        return WhisperModel(model_ref, device=self.device, compute_type=self.compute_type)

    def _build_model_for_cpu(self) -> WhisperModel:
        fallback_model_size = "tiny" if self.model_size != "tiny" else self.model_size
        model_ref = self._resolve_model_reference_for_size(fallback_model_size)
        self._model_ref = model_ref
        self._active_model_size = fallback_model_size
        logger.info("Reload WhisperModel en CPU avec ref: %s", model_ref)
        return WhisperModel(model_ref, device="cpu", compute_type="int8")

    def _resolve_model_reference(self) -> str:
        return self._resolve_model_reference_for_size(self.model_size)

    def _resolve_model_reference_for_size(self, model_size: str) -> str:
        local_candidate = Path(model_size)
        if local_candidate.exists() and local_candidate.is_dir():
            return str(local_candidate)

        if self.cache_dir is None:
            self._notify_status(f"Chargement STT {model_size}...")
            return model_size

        model_dir = self.cache_dir / "models" / f"faster-whisper-{model_size}"
        if self._is_complete_local_model_dir(model_dir):
            self._notify_status(f"STT {model_size} pret (cache local).")
            return str(model_dir)

        self._download_local_model_dir(model_dir, model_size)
        if not self._is_complete_local_model_dir(model_dir):
            raise RuntimeError(f"model.bin introuvable apres telechargement dans {model_dir}")
        return str(model_dir)

    def _download_local_model_dir(self, model_dir: Path, model_size: str) -> None:
        repo_id = f"Systran/faster-whisper-{model_size}"
        model_dir.mkdir(parents=True, exist_ok=True)

        self._notify_status(f"Telechargement STT {model_size} (premier lancement)...")
        logger.info("Telechargement direct des fichiers STT vers %s", model_dir)

        timeout = httpx.Timeout(connect=30.0, read=None, write=30.0, pool=30.0)
        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            for filename in self._REQUIRED_MODEL_FILES:
                target = model_dir / filename
                if self._is_valid_model_file(target, filename):
                    continue
                self._notify_status(f"Telechargement STT: {filename}...")
                self._download_one_file(client, repo_id, filename, target)

    def _download_one_file(self, client: httpx.Client, repo_id: str, filename: str, target: Path) -> None:
        url = hf_hub_url(repo_id=repo_id, filename=filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            tmp_target = target.with_suffix(target.suffix + ".part")
            if tmp_target.exists():
                tmp_target.unlink()

            logger.info("Download %s -> %s (attempt %s)", filename, target, attempt + 1)
            with client.stream("GET", url) as response:
                response.raise_for_status()
                with tmp_target.open("wb") as stream:
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        if chunk:
                            stream.write(chunk)
            tmp_target.replace(target)
            if self._is_valid_model_file(target, filename):
                return

        raise RuntimeError(f"Fichier STT invalide apres telechargement: {target}")

    def _is_complete_local_model_dir(self, model_dir: Path) -> bool:
        return all(self._is_valid_model_file(model_dir / filename, filename) for filename in self._REQUIRED_MODEL_FILES)

    @staticmethod
    def _is_valid_model_file(path: Path, filename: str) -> bool:
        try:
            if not path.exists() or not path.is_file():
                return False
            size = path.stat().st_size
            if filename == "model.bin":
                return size > 1_000_000
            return size > 0
        except OSError:
            return False

    @staticmethod
    def _prepare_samples(samples: np.ndarray) -> np.ndarray:
        prepared = samples.astype(np.float32, copy=True)
        if prepared.size == 0:
            return prepared

        prepared -= float(np.mean(prepared))
        peak = float(np.max(np.abs(prepared)))
        if peak > 0.0 and peak < 0.3:
            gain = min(4.0, 0.95 / peak)
            prepared *= gain

        return np.clip(prepared, -1.0, 1.0)

    def _purge_model_cache(self) -> None:
        if self.cache_dir is None:
            return

        managed_dir = self.cache_dir / "models" / f"faster-whisper-{self.model_size}"
        if managed_dir.exists():
            logger.warning("Suppression dossier modele local Whisper: %s", managed_dir)
            shutil.rmtree(managed_dir, ignore_errors=True)

        hf_cache_dir = self.cache_dir / f"models--Systran--faster-whisper-{self.model_size}"
        if hf_cache_dir.exists():
            logger.warning("Suppression cache HF legacy: %s", hf_cache_dir)
            shutil.rmtree(hf_cache_dir, ignore_errors=True)

    @staticmethod
    def _is_model_file_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return "model.bin" in message or "unable to open file" in message or "winerror 448" in message

    def _should_fallback_to_cpu(self, exc: Exception) -> bool:
        if self._cpu_fallback_applied:
            return False

        if self.device not in {"auto", "cuda"}:
            return False

        message = str(exc).lower()
        return (
            "cublas64_12.dll" in message
            or "cudnn64" in message
            or "cuda" in message and "cannot be loaded" in message
        )

    def _notify_status(self, message: str) -> None:
        if self._on_status is not None:
            self._on_status(message)

    def _preflight_force_cpu_if_needed(self) -> None:
        if self.device not in {"auto", "cuda"}:
            return
        if self._has_cuda_runtime():
            return

        self.device = "cpu"
        self.compute_type = "int8"
        self._auto_forced_cpu = True
        self._notify_status("CUDA indisponible detecte au demarrage. STT force sur CPU.")
        logger.warning("CUDA indisponible detecte en preflight. Device STT force sur CPU.")

    @staticmethod
    def _has_cuda_runtime() -> bool:
        try:
            ctypes.WinDLL("cublas64_12.dll")
            return True
        except Exception:  # pylint: disable=broad-exception-caught
            return False

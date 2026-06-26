from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from difflib import SequenceMatcher

import numpy as np

from config.settings import AppConfig
from core.audio_capture import SystemAudioCapture
from core.ollama_client import OllamaConfig, OllamaTranslator
from core.stt_engine import FasterWhisperEngine
from core.url_stream_capture import UrlAudioCapture, UrlStreamConfig


logger = logging.getLogger(__name__)


class TranslationPipeline:
    def __init__(
        self,
        config: AppConfig,
        on_translation: Callable[[str, str], None],
        on_error: Callable[[str], None] | None = None,
        on_status: Callable[[str], None] | None = None,
        on_debug: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.on_translation = on_translation
        self.on_error = on_error
        self.on_status = on_status
        self.on_debug = on_debug

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="aura-traduction-pipeline", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=max(4.0, self.config.audio.chunk_seconds * 3.0 + 2.0))

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run_loop(self) -> None:
        self._enforce_runtime_safety_bounds()
        audio_engine = self._build_audio_engine()

        try:
            self._status(
                "[Phase 1/5] Initialisation STT local (premier lancement: telechargement possible)..."
            )
            stt_engine = FasterWhisperEngine(
                model_size=self.config.stt.model_size,
                device=self.config.stt.device,
                compute_type=self.config.stt.compute_type,
                language=self.config.stt.language,
                cache_dir=self.config.stt.cache_dir or None,
                on_status=self._status,
            )

            self._status("[Phase 2/5] Initialisation du traducteur local...")
            translator = OllamaTranslator(
                OllamaConfig(
                    host=self.config.translation.host,
                    model=self.config.translation.live_model,
                    system_prompt=self.config.translation.system_prompt,
                )
            )

            self._status("[Phase 3/5] Verification service Ollama...")
            translator.ensure_service()

            available_models = translator.discover_local_model_names(self.config.translation.host)
            selected_model = translator.pick_live_translation_model(
                model_names=available_models,
                preferred=self.config.translation.live_model,
            )
            if selected_model and selected_model != self.config.translation.live_model:
                self._status(
                    f"Modele traduction trop lent/non optimal detecte. Bascule auto vers: {selected_model}"
                )
                self._debug(
                    f"Model switch auto live: requested={self.config.translation.live_model}, selected={selected_model}, "
                    f"available={available_models}"
                )
                self.config.translation.live_model = selected_model
                self.config.translation.model = selected_model
                translator = OllamaTranslator(
                    OllamaConfig(
                        host=self.config.translation.host,
                        model=selected_model,
                        system_prompt=self.config.translation.system_prompt,
                    )
                )

            self._status("[Phase 4/5] Ollama pret. Recherche de la source audio...")
            audio_engine.start()

            self._status(
                "[Phase 5/5] Audio capte depuis: "
                f"{audio_engine.active_device_name or self.config.audio.input_device or 'detection automatique'}"
            )
            self._status("Pret. En ecoute du son systeme...")
            self._debug(
                "Pipeline config: "
                f"sample_rate={self.config.audio.sample_rate}, "
                f"chunk_seconds={self.config.audio.chunk_seconds}, "
                f"silence_threshold={self.config.audio.silence_threshold}, "
                f"stt_model={self.config.stt.model_size}, "
                    f"ollama_live_model={self.config.translation.live_model}"
            )
            logger.info("Pipeline live demarree.")

            total_chunks = 0
            silent_chunks = 0
            empty_stt_streak = 0
            consecutive_silent_chunks = 0
            url_live_mode = (getattr(self.config.audio, "url_live_mode", "low-latency") or "low-latency").strip().lower()
            adaptive_merge_len = 3 if url_live_mode == "high-quality" else 2
            recent_audio_chunks: deque[np.ndarray] = deque(maxlen=adaptive_merge_len)
            last_english_text = ""
            translate_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aura-tr")
            pending_future: Future[str] | None = None
            pending_english_text = ""
            pending_started_at = 0.0
            queued_english_texts: deque[str] = deque()
            max_translation_wait_seconds = 12.0
            pending_fragment = ""

            def _reset_translate_executor(reason: str) -> None:
                nonlocal translate_executor
                self._debug(f"Reset worker traduction: {reason}")
                try:
                    translate_executor.shutdown(wait=False, cancel_futures=True)
                except Exception as shutdown_exc:  # pylint: disable=broad-exception-caught
                    self._debug(f"Erreur shutdown worker traduction: {shutdown_exc!r}")
                translate_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aura-tr")

            def _submit_translation(text_to_translate: str) -> tuple[Future[str], str]:
                self._debug(f"Soumission traduction async: {text_to_translate[:100]!r}")
                self._status("[Stabilite]Intermediaire")
                future = translate_executor.submit(
                    translator.translate,
                    text_to_translate,
                    self.config.languages.source,
                    self.config.languages.target,
                )
                return future, text_to_translate

            while not self._stop_event.is_set():
                if pending_future is not None and pending_future.done():
                    try:
                        translated_text = pending_future.result()
                    except Exception as tr_exc:  # pylint: disable=broad-exception-caught
                        self._debug(f"Traduction async en erreur ({pending_english_text!r}): {tr_exc!r}")
                        translated_text = ""

                    if not translated_text:
                        self._status("Traduction indisponible temporairement. Sous-titre précédent conservé.")
                        translated_text = ""

                    if translated_text:
                        if self._queue_has_extension(pending_english_text, queued_english_texts):
                            self._debug(
                                "Traduction ignoree (version partielle) car une extension plus complete est deja en file."
                            )
                        else:
                            self._status("Traduction reçue. Affichage overlay...")
                            self._status("[Stabilite]Stable")
                            self.on_translation(pending_english_text, translated_text)
                    else:
                        self._status("[Stabilite]Instable")
                    pending_future = None
                    pending_english_text = ""
                    pending_started_at = 0.0

                    if queued_english_texts:
                        next_text = queued_english_texts.popleft()
                        pending_future, pending_english_text = _submit_translation(next_text)
                        pending_started_at = time.monotonic()

                if pending_future is not None and pending_started_at > 0.0:
                    # Avoid keeping stale translations forever when the model becomes overloaded.
                    waiting_time = time.monotonic() - pending_started_at
                    if waiting_time >= max_translation_wait_seconds:
                        self._debug(
                            f"Traduction annulee (timeout {waiting_time:.2f}s) pour: {pending_english_text!r}"
                        )
                        cancelled = pending_future.cancel()
                        if not cancelled:
                            _reset_translate_executor("future bloquee non annulable")
                        pending_future = None
                        pending_english_text = ""
                        pending_started_at = 0.0
                        if queued_english_texts:
                            next_text = queued_english_texts.popleft()
                            pending_future, pending_english_text = _submit_translation(next_text)
                            pending_started_at = time.monotonic()

                chunk = audio_engine.read_chunk()
                total_chunks += 1
                rms = self._rms(chunk.samples)
                if not self._looks_like_speech_chunk(chunk.samples, rms, self.config.audio.silence_threshold):
                    silent_chunks += 1
                    consecutive_silent_chunks += 1
                    if consecutive_silent_chunks >= 2:
                        recent_audio_chunks.clear()
                    continue

                if self._is_silence(chunk.samples, self.config.audio.silence_threshold):
                    silent_chunks += 1
                    consecutive_silent_chunks += 1
                    if consecutive_silent_chunks >= 2:
                        recent_audio_chunks.clear()
                    if total_chunks % 20 == 0:
                        self._debug(
                            f"Stats chunks: total={total_chunks}, silence={silent_chunks}, "
                            f"non_silence={total_chunks - silent_chunks}, rms_last={rms:.6f}"
                        )
                    continue

                consecutive_silent_chunks = 0
                if recent_audio_chunks.maxlen != adaptive_merge_len:
                    recent_audio_chunks = deque(recent_audio_chunks, maxlen=adaptive_merge_len)
                recent_audio_chunks.append(chunk.samples)
                merged_samples = np.concatenate(list(recent_audio_chunks)) if recent_audio_chunks else chunk.samples

                self._status("Audio détecté. Transcription en cours...")
                self._debug(
                    f"Chunk non silencieux: rms={rms:.6f}, samples={chunk.samples.size}, "
                    f"merged_samples={merged_samples.size}"
                )
                english_text = stt_engine.transcribe(merged_samples)
                if not english_text:
                    if merged_samples.size != chunk.samples.size:
                        self._debug("STT vide sur fenetre fusionnee; nouvelle tentative sur chunk brut.")
                        english_text = stt_engine.transcribe(chunk.samples)

                if not english_text:
                    empty_stt_streak += 1
                    if empty_stt_streak >= 4:
                        self._debug(
                            f"STT vide en serie ({empty_stt_streak}). Tentative mode relaxe sur fenetre fusionnee."
                        )
                        english_text = stt_engine.transcribe_relaxed(merged_samples)
                        if english_text:
                            empty_stt_streak = 0
                            adaptive_merge_len = max(2, adaptive_merge_len - 1)

                if not english_text:
                    self._status("Audio capté mais aucun texte clair détecté.")
                    self._debug("STT vide (aucun texte reconnu sur chunk non silencieux).")
                    if empty_stt_streak >= 3:
                        max_merge = 5 if url_live_mode == "high-quality" else 4
                        adaptive_merge_len = min(max_merge, adaptive_merge_len + 1)
                    continue

                empty_stt_streak = 0
                min_merge = 3 if url_live_mode == "high-quality" else 2
                adaptive_merge_len = max(min_merge, adaptive_merge_len - 1)

                if self._is_duplicate_live_text(english_text, last_english_text):
                    self._debug(f"STT ignoré car doublon live: {english_text!r}")
                    continue

                last_english_text = english_text
                prepared_text = self._prepare_text_for_translation(english_text)
                if not prepared_text:
                    self._debug(f"STT ignoré car texte trop bruité/non exploitable: {english_text!r}")
                    continue

                if pending_fragment:
                    prepared_text = self._merge_fragmented_text(pending_fragment, prepared_text)
                    pending_fragment = ""

                if self._looks_like_incomplete_fragment(prepared_text):
                    pending_fragment = prepared_text
                    self._debug(f"Fragment detecte, attente chunk suivant: {prepared_text!r}")
                    continue

                self._status(f"EN: {english_text[:140]}")
                # Do not push English text directly to overlay: keep target language as the primary output.

                if pending_future is None:
                    pending_future, pending_english_text = _submit_translation(prepared_text)
                    pending_started_at = time.monotonic()
                else:
                    if pending_english_text and self._is_extension(prepared_text, pending_english_text):
                        prepared_text = self._merge_fragmented_text(pending_english_text, prepared_text)

                    if queued_english_texts and self._is_extension(prepared_text, queued_english_texts[-1]):
                        queued_english_texts[-1] = self._merge_fragmented_text(queued_english_texts[-1], prepared_text)
                    elif not queued_english_texts or queued_english_texts[-1] != prepared_text:
                        queued_english_texts.append(prepared_text)
                        if len(queued_english_texts) > 80:
                            dropped = queued_english_texts.popleft()
                            self._debug(f"File traduction surchargee, suppression ancien segment: {dropped!r}")
                    self._debug(
                        f"Traduction en cours, phrase mise en file (taille={len(queued_english_texts)})."
                    )

        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("Erreur pipeline: %s", exc)
            self._status(f"Erreur: {exc}")
            self._debug(f"Exception pipeline: {exc!r}")
            if self.on_error is not None:
                self.on_error(str(exc))
        finally:
            if "pending_future" in locals() and pending_future is not None:
                pending_future.cancel()
            if "translate_executor" in locals():
                translate_executor.shutdown(wait=False, cancel_futures=True)
            audio_engine.stop()
            logger.info("Pipeline live arretee.")
            self._status("Capture arrêtée.")

    def _build_audio_engine(self):
        source_mode = (getattr(self.config.audio, "source_mode", "system") or "system").strip().lower()
        if source_mode == "url" and getattr(self.config.audio, "stream_url", "").strip():
            live_mode = (getattr(self.config.audio, "url_live_mode", "low-latency") or "low-latency").strip().lower()
            chunk_seconds = self.config.audio.chunk_seconds
            if live_mode == "high-quality":
                chunk_seconds = min(3.2, max(chunk_seconds, 1.4) * 1.35)
                self._status("Source URL: mode qualite elevee active.")
            else:
                chunk_seconds = min(1.4, max(0.6, chunk_seconds))
                self._status("Source URL: mode latence faible active.")
            self._status("Source audio URL activee (YouTube/Twitch).")
            return UrlAudioCapture(
                UrlStreamConfig(
                    url=self.config.audio.stream_url,
                    sample_rate=self.config.audio.sample_rate,
                    channels=self.config.audio.channels,
                    chunk_seconds=chunk_seconds,
                    live_mode=live_mode,
                ),
                on_status=self._status,
                on_debug=self._debug,
            )

        return SystemAudioCapture(
            sample_rate=self.config.audio.sample_rate,
            channels=self.config.audio.channels,
            chunk_seconds=self.config.audio.chunk_seconds,
            input_device=self.config.audio.input_device,
        )

    @staticmethod
    def _is_silence(samples: np.ndarray, threshold: float) -> bool:
        if samples.size == 0:
            return True
        rms = float(np.sqrt(np.mean(np.square(samples))))
        return rms < threshold

    @staticmethod
    def _rms(samples: np.ndarray) -> float:
        if samples.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(samples))))

    @staticmethod
    def _looks_like_speech_chunk(samples: np.ndarray, rms: float, silence_threshold: float) -> bool:
        if samples.size < 256:
            return False

        # Reject very low-energy segments quickly.
        if rms <= (silence_threshold * 1.15):
            return False

        # A speech-like chunk usually has non-trivial zero crossing activity and
        # enough mid-energy samples (not only spikes/clicks).
        centered = samples - float(np.mean(samples))
        sign_changes = np.sum(np.abs(np.diff(np.signbit(centered))))
        zcr = float(sign_changes) / max(1, centered.size - 1)
        if zcr < 0.004 or zcr > 0.36:
            return False

        peak = float(np.max(np.abs(centered))) if centered.size > 0 else 0.0
        if peak <= 1e-8:
            return False

        energy_ratio = rms / peak
        if energy_ratio < 0.08:
            return False

        return True

    @staticmethod
    def _normalize_live_text(text: str) -> str:
        return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in text).split())

    @classmethod
    def _is_duplicate_live_text(cls, current_text: str, previous_text: str) -> bool:
        current = cls._normalize_live_text(current_text)
        previous = cls._normalize_live_text(previous_text)
        if not current:
            return True
        if current == previous:
            return True
        if not previous:
            return False

        if previous and current == previous:
            return True

        similarity = SequenceMatcher(None, current, previous).ratio()
        if similarity >= 0.97 and abs(len(current) - len(previous)) <= 8 and min(len(current), len(previous)) >= 20:
            return True

        return False

    @classmethod
    def _prepare_text_for_translation(cls, text: str) -> str:
        normalized = " ".join(text.strip().split())
        if len(normalized) < 3:
            return ""

        # Compress extreme repeated patterns like "P-P-P-P-P" from noisy STT.
        normalized = re.sub(r"\b([A-Za-z])(?:[-\s]*\1){5,}\b", r"\1", normalized)
        normalized = re.sub(r"\b(\w+)\b(?:\s+\1\b){4,}", r"\1", normalized, flags=re.IGNORECASE)

        # Reject only clearly pathological strings (mostly punctuation/hyphen artifacts).
        alpha_chars = sum(ch.isalpha() for ch in normalized)
        if alpha_chars < 3:
            return ""
        punct_like = sum(1 for ch in normalized if ch in "-_'`.,:;!?/")
        if punct_like > alpha_chars * 2:
            return ""

        if len(normalized) > 600:
            normalized = normalized[:600].rstrip(" ,;:-")

        compact = cls._normalize_live_text(normalized)
        if len(compact) < 3:
            return ""

        return normalized

    @classmethod
    def _looks_like_incomplete_fragment(cls, text: str) -> bool:
        normalized = text.strip()
        if not normalized:
            return True

        if normalized.endswith("..."):
            return True

        words = [w for w in normalized.split() if w]
        if not words:
            return True

        if words[-1].endswith("-"):
            return True
        if len(words[-1]) <= 3 and len(words) >= 3 and normalized[-1].isalnum():
            return True

        last_char = normalized[-1]
        has_terminal_punctuation = last_char in ".!?;:"
        if has_terminal_punctuation:
            return False

        return len(words) <= 6

    @classmethod
    def _is_extension(cls, candidate: str, base: str) -> bool:
        base_norm = cls._normalize_live_text(base)
        cand_norm = cls._normalize_live_text(candidate)
        if not base_norm or not cand_norm:
            return False
        if cand_norm == base_norm:
            return False
        return cand_norm.startswith(base_norm) and len(cand_norm) > len(base_norm)

    @classmethod
    def _queue_has_extension(cls, base_text: str, queued_texts: deque[str]) -> bool:
        return any(cls._is_extension(item, base_text) for item in queued_texts)

    @classmethod
    def _merge_fragmented_text(cls, previous_fragment: str, next_fragment: str) -> str:
        prev_words = previous_fragment.strip().split()
        next_words = next_fragment.strip().split()
        if not prev_words:
            return next_fragment.strip()
        if not next_words:
            return previous_fragment.strip()

        overlap = 0
        max_overlap = min(len(prev_words), len(next_words), 8)
        for size in range(max_overlap, 0, -1):
            if [w.lower() for w in prev_words[-size:]] == [w.lower() for w in next_words[:size]]:
                overlap = size
                break

        merged = prev_words + next_words[overlap:]
        return " ".join(merged).strip()

    def _status(self, message: str) -> None:
        logger.info(message)
        if self.on_status is not None:
            self.on_status(message)

    def _debug(self, message: str) -> None:
        logger.debug(message)
        if self.on_debug is not None:
            self.on_debug(message)

    def _enforce_runtime_safety_bounds(self) -> None:
        changed: list[str] = []
        if self.config.audio.chunk_seconds < 0.4:
            self.config.audio.chunk_seconds = 0.4
            changed.append("audio.chunk_seconds=0.4")
        elif self.config.audio.chunk_seconds > 4.0:
            self.config.audio.chunk_seconds = 4.0
            changed.append("audio.chunk_seconds=4.0")

        if self.config.audio.silence_threshold < 0.0001:
            self.config.audio.silence_threshold = 0.0001
            changed.append("audio.silence_threshold=0.0001")
        elif self.config.audio.silence_threshold > 0.05:
            self.config.audio.silence_threshold = 0.05
            changed.append("audio.silence_threshold=0.05")

        if not self.config.translation.live_model.strip():
            self.config.translation.live_model = "llama3.2:3b"
            changed.append("translation.live_model=llama3.2:3b")

        if not self.config.translation.file_model.strip():
            self.config.translation.file_model = "llama3.1:8b"
            changed.append("translation.file_model=llama3.1:8b")

        if not self.config.translation.model.strip():
            self.config.translation.model = self.config.translation.live_model
            changed.append(f"translation.model={self.config.translation.live_model}")

        if changed:
            self._status("Parametres ajustés automatiquement pour rester valides.")
            self._debug("Runtime safety bounds applied: " + ", ".join(changed))

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from collections.abc import Callable
from dataclasses import dataclass

from config.settings import AppConfig
from core.ollama_client import OllamaConfig, OllamaTranslator
from core.stt_engine import FasterWhisperEngine, TimedSegment


logger = logging.getLogger(__name__)


@dataclass
class MediaTranslationArtifacts:
    text_output: str
    srt_output: str
    translated_segments: list[tuple[TimedSegment, str, float]]


class MediaTranslationService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def translate_media_to_text(
        self,
        media_path: str | Path,
        on_status: Callable[[str], None] | None = None,
        on_progress: Callable[[int, str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> str:
        artifacts = self.translate_media_to_artifacts(
            media_path,
            on_status=on_status,
            on_progress=on_progress,
            cancel_event=cancel_event,
        )
        return artifacts.text_output

    def translate_media_to_artifacts(
        self,
        media_path: str | Path,
        on_status: Callable[[str], None] | None = None,
        on_progress: Callable[[int, str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> MediaTranslationArtifacts:
        source_path = Path(media_path)
        if not source_path.exists():
            raise FileNotFoundError(f"Fichier media introuvable: {source_path}")

        self._emit_progress(on_progress, 3, "Initialisation STT pour fichier media...")
        if on_status is not None:
            on_status("Initialisation STT pour fichier media...")

        stt_engine = FasterWhisperEngine(
            model_size=self.config.stt.model_size,
            device=self.config.stt.device,
            compute_type=self.config.stt.compute_type,
            language=self.config.stt.language,
            cache_dir=self.config.stt.cache_dir or None,
            on_status=on_status,
        )

        self._ensure_not_cancelled(cancel_event)
        self._emit_progress(on_progress, 12, "Analyse audio du fichier en cours...")
        stt_done_event = threading.Event()

        def _stt_heartbeat() -> None:
            started = time.monotonic()
            while not stt_done_event.wait(1.5):
                elapsed = int(time.monotonic() - started)
                # Keep user feedback moving during long STT analysis.
                heartbeat_progress = min(40, 12 + elapsed // 3)
                self._emit_progress(
                    on_progress,
                    heartbeat_progress,
                    f"Analyse audio en cours... ({elapsed}s)",
                )

        heartbeat_thread = threading.Thread(
            target=_stt_heartbeat,
            name="aura-media-stt-heartbeat",
            daemon=True,
        )
        heartbeat_thread.start()

        try:
            timed_segments = stt_engine.transcribe_file_timed_segments(
                source_path,
                on_status=on_status,
                on_progress=lambda ratio, message: self._emit_progress(
                    on_progress,
                    12 + int(max(0.0, min(1.0, ratio)) * 33),
                    message,
                ),
            )
        finally:
            stt_done_event.set()
        if not timed_segments:
            return MediaTranslationArtifacts(text_output="", srt_output="", translated_segments=[])

        self._emit_progress(on_progress, 45, f"Transcription terminee ({len(timed_segments)} segments).")
        self._ensure_not_cancelled(cancel_event)

        translator = OllamaTranslator(
            OllamaConfig(
                host=self.config.translation.host,
                model=self.config.translation.file_model,
                system_prompt=self.config.translation.system_prompt,
            )
        )
        translator.ensure_service()

        source_label = self.config.languages.source.strip() or "Source"
        target_label = self.config.languages.target.strip() or "Cible"

        translated_lines: list[str] = []
        translated_timed: list[tuple[TimedSegment, str, float]] = []
        total = len(timed_segments)
        for index, timed_segment in enumerate(timed_segments, start=1):
            self._ensure_not_cancelled(cancel_event)
            if on_status is not None:
                on_status(f"Traduction fichier: segment {index}/{total}...")
            progress = 45 + int((index / max(1, total)) * 55)
            self._emit_progress(on_progress, progress, f"Traduction segment {index}/{total}...")

            segment = timed_segment.text

            translated = self._translate_with_retry(
                translator,
                segment,
                retries=2,
                wait_seconds=0.35,
            )
            safe_translated = translated.strip() if translated else ""
            if safe_translated and translator._is_target_mismatch(
                safe_translated,
                segment,
                self.config.languages.source,
                self.config.languages.target,
            ):
                safe_translated = ""
            if not safe_translated:
                # Preserve segment in output even when translation fails to keep a complete review file.
                safe_translated = f"[NON TRADUIT] {segment.strip()}"

            confidence = self._estimate_confidence(segment, safe_translated)

            translated_timed.append((timed_segment, safe_translated, confidence))

            translated_lines.append(
                "\n".join(
                    [
                        f"Segment {index}/{total}",
                        f"Time: {timed_segment.start:.2f}s -> {timed_segment.end:.2f}s",
                        f"Confiance: {int(round(confidence * 100))}%",
                        f"{source_label}: {segment.strip()}",
                        f"{target_label}: {safe_translated}",
                        "",
                    ]
                )
            )

        self._emit_progress(on_progress, 100, "Export final en cours...")

        logger.info(
            "Traduction fichier terminee: source=%s, segments=%s, traduits=%s",
            source_path,
            total,
            len(translated_lines),
        )

        text_output = "\n".join(translated_lines).strip()
        srt_output = self._build_srt(translated_timed)
        return MediaTranslationArtifacts(
            text_output=text_output,
            srt_output=srt_output,
            translated_segments=translated_timed,
        )

    @staticmethod
    def _emit_progress(on_progress: Callable[[int, str], None] | None, percent: int, message: str) -> None:
        if on_progress is not None:
            on_progress(max(0, min(100, int(percent))), message)

    @staticmethod
    def _ensure_not_cancelled(cancel_event: threading.Event | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("Traitement annule")

    def _translate_with_retry(
        self,
        translator: OllamaTranslator,
        segment: str,
        retries: int,
        wait_seconds: float,
    ) -> str:
        attempt = 0
        while attempt <= retries:
            translated = translator.translate(
                segment,
                self.config.languages.source,
                self.config.languages.target,
            )
            if translated and translated.strip():
                return translated
            attempt += 1
            if attempt <= retries:
                time.sleep(wait_seconds)
        return ""

    @staticmethod
    def _build_srt(translated_timed: list[tuple[TimedSegment, str, float]]) -> str:
        rows: list[str] = []
        for idx, (timed_segment, translated_text, confidence) in enumerate(translated_timed, start=1):
            start = MediaTranslationService._format_srt_time(timed_segment.start)
            end_value = timed_segment.end if timed_segment.end > timed_segment.start else timed_segment.start + 1.2
            end = MediaTranslationService._format_srt_time(end_value)
            rows.append(str(idx))
            rows.append(f"{start} --> {end}")
            rows.append(f"[Confiance {int(round(confidence * 100))}%] {translated_text.strip()}")
            rows.append("")
        return "\n".join(rows).strip()

    @staticmethod
    def _estimate_confidence(source_segment: str, translated_segment: str) -> float:
        source = (source_segment or "").strip()
        translated = (translated_segment or "").strip()
        if not source or not translated:
            return 0.0
        if translated.lower().startswith("[non traduit]"):
            return 0.05

        src_words = max(1, len(source.split()))
        out_words = max(1, len(translated.split()))
        ratio = out_words / src_words

        # Heuristic confidence from verbosity ratio + punctuation completeness.
        ratio_score = max(0.0, 1.0 - abs(1.0 - min(max(ratio, 0.2), 2.4)) * 0.55)
        punctuation_bonus = 0.12 if translated.endswith((".", "!", "?")) else 0.0
        length_bonus = 0.08 if out_words >= 3 else 0.0
        confidence = min(0.99, max(0.08, ratio_score * 0.8 + punctuation_bonus + length_bonus))
        return confidence

    @staticmethod
    def _format_srt_time(seconds: float) -> str:
        total_ms = max(0, int(round(seconds * 1000)))
        hours = total_ms // 3_600_000
        minutes = (total_ms % 3_600_000) // 60_000
        secs = (total_ms % 60_000) // 1000
        millis = total_ms % 1000
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    @staticmethod
    def default_output_path(media_path: str | Path) -> Path:
        source_path = Path(media_path)
        return source_path.with_name(f"{source_path.stem}_traduction_complete.txt")

    @staticmethod
    def save_translation_output(output_path: str | Path, text: str) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    @staticmethod
    def default_srt_output_path(output_txt_path: str | Path) -> Path:
        txt_path = Path(output_txt_path)
        return txt_path.with_suffix(".srt")

    @staticmethod
    def save_srt_output(output_path: str | Path, srt_text: str) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(srt_text, encoding="utf-8")
        return path

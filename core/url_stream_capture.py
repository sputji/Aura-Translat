from __future__ import annotations

import logging
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass

import imageio_ffmpeg
import numpy as np
import yt_dlp

from core.audio_capture import AudioChunk

logger = logging.getLogger(__name__)


@dataclass
class UrlStreamConfig:
    url: str
    sample_rate: int
    channels: int
    chunk_seconds: float
    live_mode: str = "low-latency"


class UrlAudioCapture:
    def __init__(
        self,
        config: UrlStreamConfig,
        on_status: Callable[[str], None] | None = None,
        on_debug: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self._on_status = on_status
        self._on_debug = on_debug
        self.active_device_name = ""
        self._process: subprocess.Popen | None = None
        self._resolved_media_url: str = ""
        self._backoff_base_seconds = 0.8
        self._backoff_max_seconds = 6.0
        self._max_reconnect_attempts = 5

    def start(self) -> None:
        if not self.config.url.strip():
            raise RuntimeError("URL vide: impossible de demarrer la capture URL.")

        self._start_process()
        self.active_device_name = f"URL: {self.config.url.strip()}"
        logger.info("Capture URL demarree: %s", self.config.url.strip())

    def _start_process(self) -> None:
        self._resolved_media_url = self._resolve_media_url(self.config.url)
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "2",
            "-i",
            self._resolved_media_url,
            "-vn",
            "-ac",
            str(self.config.channels),
            "-ar",
            str(self.config.sample_rate),
            "-f",
            "f32le",
            "-acodec",
            "pcm_f32le",
            "pipe:1",
        ]

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def read_chunk(self) -> AudioChunk:
        if self._process is None or self._process.stdout is None:
            raise RuntimeError("Capture URL non demarree.")

        frames = int(self.config.sample_rate * self.config.chunk_seconds)
        bytes_per_sample = 4
        needed = frames * self.config.channels * bytes_per_sample

        payload = self._process.stdout.read(needed)
        if payload is None or len(payload) < needed:
            self._debug("Flux URL interrompu: tentative de reconnexion automatique.")
            if self._try_reconnect_with_backoff():
                if self._process is None or self._process.stdout is None:
                    raise RuntimeError("Reconnexion URL reussie mais flux indisponible.")
                payload = self._process.stdout.read(needed)
            else:
                raise RuntimeError("Flux URL interrompu. Echec reconnexion automatique.")

        if payload is None or len(payload) < needed:
            raise RuntimeError("Flux URL interrompu ou insuffisant.")

        samples = np.frombuffer(payload, dtype=np.float32)
        if self.config.channels > 1:
            samples = samples.reshape(-1, self.config.channels).mean(axis=1)

        return AudioChunk(samples=samples.astype(np.float32), sample_rate=self.config.sample_rate)

    def stop(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return

        try:
            process.terminate()
            process.wait(timeout=1.2)
        except Exception:  # pylint: disable=broad-exception-caught
            try:
                process.kill()
            except Exception:  # pylint: disable=broad-exception-caught
                pass

        logger.info("Capture URL arretee.")

    def _try_reconnect_with_backoff(self) -> bool:
        for attempt in range(1, self._max_reconnect_attempts + 1):
            delay = min(self._backoff_max_seconds, self._backoff_base_seconds * (2 ** (attempt - 1)))
            self._status(f"Flux coupe. Reconnexion ({attempt}/{self._max_reconnect_attempts})...")
            self._debug(f"Reconnexion URL tentative {attempt} apres {delay:.1f}s")
            self.stop()
            time.sleep(delay)
            try:
                self._start_process()
                self._status("Reconnexion URL reussie. Reprise de la capture.")
                return True
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self._debug(f"Reconnexion URL echouee tentative {attempt}: {exc!r}")
                continue
        return False

    def _status(self, message: str) -> None:
        if self._on_status is not None:
            self._on_status(message)

    def _debug(self, message: str) -> None:
        if self._on_debug is not None:
            self._on_debug(message)

    @staticmethod
    def _resolve_media_url(source_url: str) -> str:
        options = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "skip_download": True,
            "format": "bestaudio/best",
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(source_url.strip(), download=False)

        if isinstance(info, dict):
            direct_url = str(info.get("url") or "").strip()
            if direct_url:
                return direct_url

        raise RuntimeError("Impossible de resoudre un flux audio direct pour cette URL.")

    @classmethod
    def test_stream_url(
        cls,
        source_url: str,
        sample_rate: int = 16000,
        channels: int = 1,
        probe_seconds: float = 2.2,
    ) -> tuple[str, int]:
        if not source_url.strip():
            raise RuntimeError("URL vide.")

        resolved_media_url = cls._resolve_media_url(source_url)
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "2",
            "-i",
            resolved_media_url,
            "-vn",
            "-t",
            f"{max(1.2, float(probe_seconds)):.1f}",
            "-ac",
            str(max(1, int(channels))),
            "-ar",
            str(max(8000, int(sample_rate))),
            "-f",
            "f32le",
            "-acodec",
            "pcm_f32le",
            "pipe:1",
        ]

        completed = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            timeout=18,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        payload_size = len(completed.stdout or b"")
        if completed.returncode != 0 and payload_size < 4096:
            stderr_text = (completed.stderr or b"").decode("utf-8", errors="ignore").strip()
            raise RuntimeError(stderr_text or "ffmpeg n'a pas pu lire le flux audio.")
        if payload_size < 4096:
            raise RuntimeError("Flux detecte mais audio insuffisant pendant le test (possible pub/coupure/live inactif).")

        return resolved_media_url, payload_size

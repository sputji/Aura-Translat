from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import soundcard as sc


logger = logging.getLogger(__name__)


@dataclass
class AudioChunk:
    samples: np.ndarray
    sample_rate: int


class SystemAudioCapture:
    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_seconds: float = 3.0,
        input_device: str = "",
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_seconds = chunk_seconds
        self.input_device = input_device.strip()
        self.active_device_name = ""
        self._recorder_cm = None
        self._recorder = None

    def start(self) -> None:
        loopback_device = self._pick_loopback_device()
        self.active_device_name = loopback_device.name or ""
        logger.info("Audio loopback active sur: %s", loopback_device.name)

        self._recorder_cm = loopback_device.recorder(
            samplerate=self.sample_rate,
            channels=self.channels,
            blocksize=int(self.sample_rate * self.chunk_seconds),
        )
        self._recorder = self._recorder_cm.__enter__()

    def _pick_loopback_device(self) -> sc.Microphone:
        loopbacks = sc.all_microphones(include_loopback=True)
        if not loopbacks:
            raise RuntimeError("Aucun peripherique loopback detecte sur cette machine.")

        if self.input_device:
            wanted = self.input_device.lower()
            for mic in loopbacks:
                if (mic.name or "").strip().lower() == wanted:
                    return mic
            for mic in loopbacks:
                if wanted in (mic.name or "").strip().lower():
                    return mic

        speaker = sc.default_speaker()
        if speaker is not None:
            speaker_name = (speaker.name or "").strip().lower()

            # Prefer an exact name match with the current default speaker.
            for mic in loopbacks:
                if (mic.name or "").strip().lower() == speaker_name:
                    return mic

            # Then try a partial name match to handle vendor-added suffixes.
            for mic in loopbacks:
                if speaker_name and speaker_name in (mic.name or "").strip().lower():
                    return mic

        # Fallback to the first available loopback capture device.
        return loopbacks[0]

    @staticmethod
    def list_loopback_devices() -> list[str]:
        devices = sc.all_microphones(include_loopback=True)
        return [device.name for device in devices if device.name]

    def read_chunk(self) -> AudioChunk:
        if self._recorder is None:
            raise RuntimeError("Le moteur audio n'est pas demarre.")

        frames = int(self.sample_rate * self.chunk_seconds)
        data = self._recorder.record(numframes=frames)

        if data.ndim > 1:
            data = np.mean(data, axis=1)

        data = data.astype(np.float32)
        return AudioChunk(samples=data, sample_rate=self.sample_rate)

    def stop(self) -> None:
        if self._recorder is not None:
            if self._recorder_cm is not None:
                self._recorder_cm.__exit__(None, None, None)
            self._recorder_cm = None
            self._recorder = None

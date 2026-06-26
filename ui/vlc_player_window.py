from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class VlcPlayerWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Aura-Traduction - Lecteur VLC")
        self.setMinimumSize(980, 640)

        self._container = QWidget(self)
        self._container.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self._status = QLabel("Lecteur VLC prêt.", self)
        self._status.setWordWrap(True)

        self._play_pause_button = QPushButton("⏯", self)
        self._play_pause_button.setToolTip("Lecture / Pause")
        self._play_pause_button.setFixedWidth(44)
        self._play_pause_button.clicked.connect(self._toggle_play_pause)
        self._back_button = QPushButton("⏪", self)
        self._back_button.setToolTip("Reculer de 10 secondes")
        self._back_button.setFixedWidth(44)
        self._back_button.clicked.connect(lambda: self._seek_relative(-10_000))
        self._forward_button = QPushButton("⏩", self)
        self._forward_button.setToolTip("Avancer de 10 secondes")
        self._forward_button.setFixedWidth(44)
        self._forward_button.clicked.connect(lambda: self._seek_relative(10_000))

        self._seek_slider = QSlider(Qt.Orientation.Horizontal, self)
        self._seek_slider.setRange(0, 1000)
        self._seek_slider.setTracking(False)
        self._seek_slider.sliderReleased.connect(self._apply_seek_from_slider)

        self._time_label = QLabel("00:00 / 00:00", self)

        self._volume_slider = QSlider(Qt.Orientation.Horizontal, self)
        self._volume_slider.setRange(0, 120)
        self._volume_slider.setValue(85)
        self._volume_slider.valueChanged.connect(self._on_volume_changed)
        self._volume_label = QLabel("🔊 85%", self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(self._container, stretch=1)

        controls_row = QHBoxLayout()
        controls_row.setContentsMargins(0, 0, 0, 0)
        controls_row.setSpacing(6)
        controls_row.addWidget(self._back_button)
        controls_row.addWidget(self._play_pause_button)
        controls_row.addWidget(self._forward_button)
        controls_row.addWidget(self._seek_slider, stretch=1)
        controls_row.addWidget(self._time_label)
        controls_row.addWidget(self._volume_label)
        controls_row.addWidget(self._volume_slider)
        layout.addLayout(controls_row)
        layout.addWidget(self._status)

        self._vlc_module = None
        self._vlc_instance = None
        self._player = None
        self._updating_seek = False

        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(220)
        self._ui_timer.timeout.connect(self._refresh_playback_ui)

        try:
            import vlc  # type: ignore

            self._vlc_module = vlc
            self._vlc_instance = vlc.Instance("--no-video-title-show", "--quiet")
            self._player = self._vlc_instance.media_player_new()
            self._player.audio_set_volume(self._volume_slider.value())
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("VLC indisponible: %s", exc)
            self._status.setText(
                "VLC indisponible. Installe python-vlc + VLC Desktop, puis relance l'application."
            )

    def open_media(self, media_path: str | Path, subtitle_path: str | Path | None = None) -> bool:
        media_file = str(Path(media_path).resolve())
        if self._vlc_instance is None or self._player is None:
            self._status.setText("Lecture impossible: moteur VLC non disponible.")
            return False

        try:
            media = self._vlc_instance.media_new(media_file)
            self._player.set_media(media)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("Erreur chargement media VLC: %s", exc)
            self._status.setText("Lecture impossible: echec chargement media dans VLC.")
            return False

        try:
            win_id = int(self._container.winId())
            if hasattr(self._player, "set_hwnd"):
                self._player.set_hwnd(win_id)
            elif hasattr(self._player, "set_xwindow"):
                self._player.set_xwindow(win_id)
            elif hasattr(self._player, "set_nsobject"):
                self._player.set_nsobject(win_id)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("Erreur binding fenetre VLC: %s", exc)
            self._status.setText("Lecture impossible: echec liaison fenetre VLC.")
            return False

        subtitle_file = Path(subtitle_path).resolve() if subtitle_path else None
        if subtitle_file is not None and subtitle_file.exists():
            try:
                self._player.video_set_subtitle_file(str(subtitle_file))
                self._status.setText(f"Lecture: {Path(media_file).name} | Sous-titres: {subtitle_file.name}")
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("Impossible de charger les sous-titres VLC: %s", exc)
                self._status.setText(f"Lecture: {Path(media_file).name} | Sous-titres non charges")
        else:
            self._status.setText(f"Lecture: {Path(media_file).name}")

        # Start playback a tiny bit later to let native window handles settle.
        QTimer.singleShot(80, self._safe_play)
        self._ui_timer.start()
        return True

    def _safe_play(self) -> None:
        try:
            if self._player is not None:
                self._player.play()
                self._play_pause_button.setText("⏯")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("Erreur lancement lecture VLC: %s", exc)
            self._status.setText("Lecture impossible: erreur au demarrage VLC.")

    def _toggle_play_pause(self) -> None:
        if self._player is None:
            return
        try:
            if self._player.is_playing():
                self._player.pause()
                self._play_pause_button.setText("▶")
            else:
                self._player.play()
                self._play_pause_button.setText("⏸")
        except Exception:  # pylint: disable=broad-exception-caught
            logger.debug("Erreur play/pause VLC", exc_info=True)

    def _seek_relative(self, delta_ms: int) -> None:
        if self._player is None:
            return
        try:
            current = max(0, int(self._player.get_time()))
            total = max(0, int(self._player.get_length()))
            target = max(0, current + delta_ms)
            if total > 0:
                target = min(target, total)
            self._player.set_time(target)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.debug("Erreur seek relatif VLC", exc_info=True)

    def _apply_seek_from_slider(self) -> None:
        if self._player is None:
            return
        total = max(0, int(self._player.get_length()))
        if total <= 0:
            return
        ratio = self._seek_slider.value() / 1000.0
        target = int(total * ratio)
        try:
            self._player.set_time(target)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.debug("Erreur seek slider VLC", exc_info=True)

    def _on_volume_changed(self, value: int) -> None:
        self._volume_label.setText(f"🔊 {value}%")
        if self._player is None:
            return
        try:
            self._player.audio_set_volume(int(value))
        except Exception:  # pylint: disable=broad-exception-caught
            logger.debug("Erreur volume VLC", exc_info=True)

    def _refresh_playback_ui(self) -> None:
        if self._player is None:
            return

        try:
            current = max(0, int(self._player.get_time()))
            total = max(0, int(self._player.get_length()))
            if not self._seek_slider.isSliderDown() and total > 0:
                self._updating_seek = True
                self._seek_slider.setValue(int((current / total) * 1000))
                self._updating_seek = False

            self._time_label.setText(f"{self._format_ms(current)} / {self._format_ms(total)}")
            self._play_pause_button.setText("⏸" if self._player.is_playing() else "▶")
        except Exception:  # pylint: disable=broad-exception-caught
            logger.debug("Erreur refresh UI VLC", exc_info=True)

    @staticmethod
    def _format_ms(value_ms: int) -> str:
        seconds = max(0, value_ms // 1000)
        minutes = seconds // 60
        remain = seconds % 60
        return f"{minutes:02d}:{remain:02d}"

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            self._ui_timer.stop()
            if self._player is not None:
                self._player.stop()
        except Exception:  # pylint: disable=broad-exception-caught
            logger.debug("Erreur stop VLC", exc_info=True)
        super().closeEvent(event)

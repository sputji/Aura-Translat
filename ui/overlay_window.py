from __future__ import annotations

import logging
import time
from collections import deque
from typing import Callable

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from config.settings import OverlayConfig

logger = logging.getLogger(__name__)


class OverlayWindow(QWidget):
    _PLACEHOLDER_TEXT = "En attente du son système..."
    _MIN_SECONDS_BY_SPEED = {
        "slow": 2.0,
        "normal": 1.4,
        "fast": 0.9,
    }

    def __init__(self, overlay_config: OverlayConfig) -> None:
        super().__init__()

        self._drag_active = False
        self._drag_pos = QPoint()

        self._open_settings_handler: Callable[[], None] | None = None
        self._quit_handler: Callable[[], None] | None = None
        self._toggle_live_handler: Callable[[], None] | None = None
        self._hide_overlay_handler: Callable[[], None] | None = None
        self._live_paused = False
        self._background_color = QColor("#202124")
        self._pending_text = ""
        self._last_caption_update_at = 0.0
        self._min_subtitle_display_seconds = self._MIN_SECONDS_BY_SPEED["normal"]
        self._max_visible_lines = 3
        self._caption_lines: deque[str] = deque(maxlen=self._max_visible_lines)
        self._last_overlay_config = overlay_config

        self._display_timer = QTimer(self)
        self._display_timer.setSingleShot(True)
        self._display_timer.timeout.connect(self._flush_pending_caption)

        self.status_label = QLabel(self)
        self.status_label.setObjectName("overlayStatus")
        self.status_label.setWordWrap(True)
        self.status_label.setText("Aura-Traduction est prêt. Ouvre une vidéo en anglais pour afficher les sous-titres.")

        self.phase_label = QLabel(self)
        self.phase_label.setObjectName("overlayPhase")
        self.phase_label.setText("Initialisation")

        self.live_badge_label = QLabel("LIVE", self)
        self.live_badge_label.setObjectName("overlayLiveBadge")

        self.stability_badge_label = QLabel("STABLE", self)
        self.stability_badge_label.setObjectName("overlayStabilityBadge")

        self.pause_button = QPushButton("Pause", self)
        self.pause_button.setObjectName("overlayPause")
        self.pause_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pause_button.clicked.connect(self._on_toggle_live_clicked)

        self.hide_button = QPushButton("Masquer", self)
        self.hide_button.setObjectName("overlayHide")
        self.hide_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hide_button.clicked.connect(self._on_hide_overlay_clicked)

        self.settings_button = QPushButton("⚙", self)
        self.settings_button.setObjectName("overlaySettings")
        self.settings_button.setFixedWidth(32)
        self.settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_button.clicked.connect(self._on_open_settings_clicked)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(8)
        top_row.addWidget(self.phase_label)
        top_row.addWidget(self.live_badge_label)
        top_row.addWidget(self.stability_badge_label)
        top_row.addStretch(1)
        top_row.addWidget(self.hide_button)
        top_row.addWidget(self.pause_button)
        top_row.addWidget(self.settings_button)

        self.text_area = QPlainTextEdit(self)
        self.text_area.setReadOnly(True)
        self.text_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.text_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.text_area.setMinimumHeight(110)
        self.text_area.setPlainText(self._PLACEHOLDER_TEXT)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addLayout(top_row)
        layout.addWidget(self.status_label)
        layout.addWidget(self.text_area)

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.apply_overlay_config(overlay_config)

    @staticmethod
    def _normalize_speed_label(value: str) -> str:
        lowered = value.strip().lower()
        if lowered in {"lent", "slow"}:
            return "slow"
        if lowered in {"rapide", "fast"}:
            return "fast"
        return "normal"

    def set_actions(
        self,
        on_open_settings: Callable[[], None],
        on_quit: Callable[[], None],
        on_toggle_live: Callable[[], None] | None = None,
        on_hide_overlay: Callable[[], None] | None = None,
    ) -> None:
        self._open_settings_handler = on_open_settings
        self._quit_handler = on_quit
        self._toggle_live_handler = on_toggle_live
        self._hide_overlay_handler = on_hide_overlay

    def apply_overlay_config(self, cfg: OverlayConfig) -> None:
        self._last_overlay_config = cfg
        flags = Qt.WindowType.Tool
        if cfg.frameless:
            flags |= Qt.WindowType.FramelessWindowHint
        if cfg.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint

        self.setWindowFlags(flags)
        if self.isVisible():
            self.show()
        self._max_visible_lines = max(1, int(getattr(cfg, "max_visible_lines", 3)))
        self._caption_lines = deque(self._caption_lines, maxlen=self._max_visible_lines)
        speed_key = self._normalize_speed_label(getattr(cfg, "display_speed", "normal"))
        self._min_subtitle_display_seconds = self._MIN_SECONDS_BY_SPEED.get(speed_key, 1.4)

        line_height = max(18, cfg.font_size + 8)
        text_height = max(88, self._max_visible_lines * line_height)
        self.text_area.setMinimumHeight(text_height)
        self.text_area.setMaximumHeight(text_height + 24)

        status_zone_height = max(74, cfg.font_size * 2)
        overlay_height = max(cfg.height, text_height + status_zone_height)
        self.resize(cfg.width, overlay_height)

        screen = self.screen() or self.windowHandle().screen() if self.windowHandle() else None
        if screen is not None:
            geometry = screen.availableGeometry()
            x = geometry.x() + (geometry.width() - cfg.width) // 2
            y = geometry.y() + geometry.height() - cfg.height - cfg.bottom_margin
            self.move(x, y)

        opacity = min(max(cfg.background_opacity, 0.0), 1.0)
        bg = QColor(cfg.background_color)
        bg.setAlphaF(opacity)
        self._background_color = bg

        text_color = QColor(cfg.text_color)
        weight = 700 if cfg.font_bold else 400

        self.setStyleSheet(
            f"""
            QWidget {{
                background: transparent;
            }}
            QLabel#overlayStatus {{
                background: transparent;
                color: {text_color.name()};
                font-size: {max(cfg.font_size - 6, 14)}px;
                font-weight: 700;
                padding: 2px 4px;
            }}
            QLabel#overlayPhase {{
                background: rgba(255, 255, 255, 0.12);
                color: {text_color.name()};
                font-size: {max(cfg.font_size - 8, 12)}px;
                font-weight: 700;
                border-radius: 8px;
                padding: 2px 8px;
            }}
            QLabel#overlayLiveBadge {{
                color: #ffffff;
                background: {'#d04747' if self._live_paused else '#1f9d57'};
                border: 1px solid rgba(255, 255, 255, 0.28);
                border-radius: 8px;
                padding: 2px 8px;
                font-size: {max(cfg.font_size - 9, 11)}px;
                font-weight: 800;
            }}
            QLabel#overlayStabilityBadge {{
                color: #ffffff;
                background: #2f6db0;
                border: 1px solid rgba(255, 255, 255, 0.28);
                border-radius: 8px;
                padding: 2px 8px;
                font-size: {max(cfg.font_size - 9, 11)}px;
                font-weight: 800;
            }}
            QPlainTextEdit {{
                background: transparent;
                border: none;
                color: {text_color.name()};
                font-size: {cfg.font_size}px;
                font-weight: {weight};
                padding: 4px;
            }}
            QPushButton#overlayPause, QPushButton#overlaySettings, QPushButton#overlayHide {{
                color: {text_color.name()};
                background: rgba(255, 255, 255, 0.14);
                border: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: 8px;
                padding: 2px 8px;
                font-size: {max(cfg.font_size - 9, 11)}px;
                font-weight: 700;
            }}
            QPushButton#overlayPause:hover, QPushButton#overlaySettings:hover, QPushButton#overlayHide:hover {{
                background: rgba(255, 255, 255, 0.22);
            }}
            """
        )
        self.update()

    def append_translation(self, translated_text: str) -> None:
        if not translated_text.strip():
            return

        try:
            now = time.monotonic()
            elapsed = now - self._last_caption_update_at
            if self._last_caption_update_at > 0.0 and elapsed < self._min_subtitle_display_seconds:
                self._pending_text = translated_text
                delay_ms = max(20, int((self._min_subtitle_display_seconds - elapsed) * 1000))
                self._display_timer.start(delay_ms)
                return

            self._set_caption_text(translated_text)
        except Exception:
            logger.exception("Erreur append_translation")

    def _set_caption_text(self, text: str) -> None:
        cleaned = text.strip()
        if cleaned:
            if not self._caption_lines or self._caption_lines[-1] != cleaned:
                self._caption_lines.append(cleaned)

        if self._caption_lines:
            self.text_area.setPlainText("\n".join(self._caption_lines))
        else:
            self.text_area.setPlainText(self._PLACEHOLDER_TEXT)

        self._last_caption_update_at = time.monotonic()
        self._pending_text = ""

        scrollbar = self.text_area.verticalScrollBar()
        if scrollbar is not None:
            scrollbar.setValue(scrollbar.maximum())

    def _flush_pending_caption(self) -> None:
        pending = self._pending_text.strip()
        if not pending:
            return
        self._set_caption_text(pending)

    def set_status(self, status_text: str) -> None:
        normalized = status_text.strip()
        if normalized.startswith("[Stabilite]"):
            state = normalized.replace("[Stabilite]", "", 1).strip().lower()
            self.set_translation_stability(state)
            return

        if normalized.startswith("[Phase") and "]" in normalized:
            head, tail = normalized.split("]", 1)
            self.phase_label.setText(head.replace("[", "").strip())
            self.status_label.setText(tail.strip())
            return

        if normalized.lower().startswith("pret"):
            self.phase_label.setText("En direct")
            if not self._live_paused:
                self.live_badge_label.setText("LIVE")

        self.status_label.setText(normalized)

    def clear_translations(self) -> None:
        self._caption_lines.clear()
        self.text_area.setPlainText(self._PLACEHOLDER_TEXT)
        self._pending_text = ""
        self.set_translation_stability("stable")

    def set_translation_stability(self, state: str) -> None:
        normalized = (state or "").strip().lower()
        if normalized in {"intermediaire", "pending", "processing"}:
            self.stability_badge_label.setText("TEMP")
            color = "#d6a84b"
        elif normalized in {"instable", "timeout", "uncertain"}:
            self.stability_badge_label.setText("INSTABLE")
            color = "#c24f4f"
        else:
            self.stability_badge_label.setText("STABLE")
            color = "#2f6db0"

        self.stability_badge_label.setStyleSheet(
            "color: #ffffff;"
            f"background: {color};"
            "border: 1px solid rgba(255, 255, 255, 0.28);"
            "border-radius: 8px;"
            "padding: 2px 8px;"
            "font-weight: 800;"
        )

    def set_live_paused(self, paused: bool) -> None:
        self._live_paused = paused
        self.pause_button.setText("Reprendre" if paused else "Pause")
        self.live_badge_label.setText("PAUSE" if paused else "LIVE")
        self.live_badge_label.setStyleSheet(
            "color: #ffffff;"
            f"background: {'#d04747' if paused else '#1f9d57'};"
            "border: 1px solid rgba(255, 255, 255, 0.28);"
            "border-radius: 8px;"
            "padding: 2px 8px;"
            "font-weight: 800;"
        )
        if paused:
            self.phase_label.setText("En pause")
        elif self.phase_label.text().strip().lower() == "en pause":
            self.phase_label.setText("En direct")

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._background_color)
        painter.drawRoundedRect(self.rect(), 14, 14)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)

        settings_action = menu.addAction("Parametres")
        hide_action = menu.addAction("Masquer overlay")
        toggle_live_action = menu.addAction("Reprendre traduction" if self._live_paused else "Pause traduction")
        clear_action = menu.addAction("Effacer le texte")
        quit_action = menu.addAction("Quitter")

        selected = menu.exec(event.globalPos())
        if selected == settings_action and self._open_settings_handler:
            self._open_settings_handler()
        elif selected == hide_action and self._hide_overlay_handler:
            self._hide_overlay_handler()
        elif selected == toggle_live_action and self._toggle_live_handler:
            self._toggle_live_handler()
        elif selected == clear_action:
            self.clear_translations()
        elif selected == quit_action and self._quit_handler:
            self._quit_handler()

    def _on_open_settings_clicked(self) -> None:
        if self._open_settings_handler is not None:
            self._open_settings_handler()

    def _on_toggle_live_clicked(self) -> None:
        if self._toggle_live_handler is not None:
            self._toggle_live_handler()

    def _on_hide_overlay_clicked(self) -> None:
        if self._hide_overlay_handler is not None:
            self._hide_overlay_handler()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = True
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_active and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = False
            event.accept()

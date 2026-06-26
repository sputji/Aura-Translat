from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from multiprocessing import cpu_count
from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QFrame,
    QGroupBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFormLayout,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.audio_capture import SystemAudioCapture
from core.ollama_client import OllamaTranslator
from core.url_stream_capture import UrlAudioCapture
from config.settings import AppConfig, save_app_config
from ui.shortcut_settings_dialog import ShortcutSettingsDialog


logger = logging.getLogger(__name__)


class SettingsWindow(QDialog):
    def __init__(
        self,
        config: AppConfig,
        config_path: Path,
        parent=None,
        on_translate_media: Callable[[QWidget | None], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Aura-Traduction - Parametres")
        self.setMinimumWidth(760)

        self._config = config
        self._config_path = config_path
        self._on_translate_media = on_translate_media
        self._media_translation_requested = False

        self._source_combo = QComboBox()
        self._source_combo.addItems(["English", "French", "Spanish", "German", "Italian"])
        self._source_combo.setCurrentText(self._config.languages.source)

        self._audio_device_combo = QComboBox()
        self._audio_device_combo.setEditable(False)
        self._audio_refresh_btn = QPushButton("Rafraichir")
        self._audio_refresh_btn.clicked.connect(self._refresh_audio_devices)
        self._audio_source_combo = QComboBox()
        self._audio_source_combo.addItem("Audio systeme (PC)", "system")
        self._audio_source_combo.addItem("URL stream (YouTube/Twitch/autre)", "url")
        self._audio_source_combo.currentIndexChanged.connect(self._on_audio_source_changed)
        self._stream_url_edit = QLineEdit()
        self._stream_url_edit.setPlaceholderText("https://www.youtube.com/... ou https://www.twitch.tv/... ou autre URL live")
        self._test_stream_url_btn = QPushButton("Tester URL")
        self._test_stream_url_btn.clicked.connect(self._test_stream_url)
        self._url_mode_combo = QComboBox()
        self._url_mode_combo.addItem("Latence faible (live)", "low-latency")
        self._url_mode_combo.addItem("Qualite elevee (stabilite)", "high-quality")
        self._adblock_hint_label = QLabel(
            "Anti-pub recommande: active un bloqueur pub cote navigateur/source. "
            "Certaines pubs server-side ne sont pas bloquables par l'application."
        )
        self._adblock_hint_label.setStyleSheet("font-size: 12px; color: #f3c969;")
        self._adblock_hint_label.setWordWrap(True)

        self._target_combo = QComboBox()
        self._target_combo.addItems(["Francais", "English", "Spanish", "German", "Italian"])
        self._target_combo.setCurrentText(self._config.languages.target)

        self._stt_model_combo = QComboBox()
        self._stt_model_combo.addItems(["tiny", "base", "small", "medium"])
        self._stt_model_combo.setCurrentText(self._config.stt.model_size)
        self._stt_model_combo.setToolTip(
            "Niveau STT: tiny (ultra rapide, moins precis), base (equilibre), "
            "small (meilleur), medium (plus precis mais plus lent)."
        )

        self._repair_stt_cache_btn = QPushButton("Reparer cache STT")
        self._repair_stt_cache_btn.clicked.connect(self._repair_stt_cache)

        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.addItem(self._config.translation.live_model)
        self._model_combo.setCurrentText(self._config.translation.live_model)

        self._file_model_combo = QComboBox()
        self._file_model_combo.setEditable(True)
        self._file_model_combo.addItem(self._config.translation.file_model)
        self._file_model_combo.setCurrentText(self._config.translation.file_model)

        self._refresh_models_btn = QPushButton("Rafraichir")
        self._refresh_models_btn.clicked.connect(self._refresh_ollama_models)

        self._bg_color_label = QLabel(self._config.overlay.background_color)
        self._bg_color_btn = QPushButton("Choisir")
        self._bg_color_btn.clicked.connect(self._pick_background_color)

        self._text_color_label = QLabel(self._config.overlay.text_color)
        self._text_color_btn = QPushButton("Choisir")
        self._text_color_btn.clicked.connect(self._pick_text_color)

        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(10, 100)
        self._opacity_slider.setValue(int(self._config.overlay.background_opacity * 100))

        self._font_size_spin = QSpinBox()
        self._font_size_spin.setRange(12, 72)
        self._font_size_spin.setValue(self._config.overlay.font_size)

        self._font_bold_checkbox = QCheckBox("Texte en gras")
        self._font_bold_checkbox.setChecked(self._config.overlay.font_bold)

        self._display_speed_combo = QComboBox()
        self._display_speed_combo.addItem("Lent", "slow")
        self._display_speed_combo.addItem("Normal", "normal")
        self._display_speed_combo.addItem("Rapide", "fast")

        self._max_lines_spin = QSpinBox()
        self._max_lines_spin.setRange(1, 12)
        self._max_lines_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self._max_lines_spin.setSingleStep(1)
        self._max_lines_spin.setAccelerated(True)
        self._max_lines_spin.setKeyboardTracking(False)
        self._max_lines_spin.setEnabled(True)
        self._max_lines_spin.setMinimumHeight(34)
        self._max_lines_spin.setStyleSheet(
            "QSpinBox { padding-right: 24px; }"
            "QSpinBox::up-button { width: 20px; }"
            "QSpinBox::down-button { width: 20px; }"
        )

        self._max_lines_minus_btn = QPushButton("-")
        self._max_lines_plus_btn = QPushButton("+")
        self._max_lines_minus_btn.setFixedWidth(34)
        self._max_lines_plus_btn.setFixedWidth(34)
        self._max_lines_minus_btn.clicked.connect(self._max_lines_spin.stepDown)
        self._max_lines_plus_btn.clicked.connect(self._max_lines_spin.stepUp)

        self._preview_container = QFrame()
        self._preview_container.setFrameShape(QFrame.Shape.StyledPanel)
        preview_layout = QVBoxLayout(self._preview_container)
        preview_layout.setContentsMargins(10, 10, 10, 10)

        self._preview_text = QLabel("This is a live caption preview / Exemple de sous-titre en direct")
        self._preview_text.setWordWrap(True)
        self._preview_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_layout.addWidget(self._preview_text)

        capture_group = QGroupBox("Capture et transcription")
        capture_layout = QFormLayout(capture_group)
        capture_hint = QLabel(
            "Choisis la sortie audio qui joue la video/livestream. "
            "Si rien ne sort, clique sur Rafraichir puis redemarre la capture."
        )
        capture_hint.setStyleSheet("font-size: 12px;")
        capture_hint.setWordWrap(True)
        capture_layout.addRow(capture_hint)
        capture_layout.addRow("Langue source ?", self._source_combo)
        capture_layout.addRow("Mode capture ?", self._audio_source_combo)
        audio_row = QHBoxLayout()
        audio_row.addWidget(self._audio_device_combo, stretch=1)
        audio_row.addWidget(self._audio_refresh_btn)
        capture_layout.addRow("Source audio ?", audio_row)
        stream_row = QHBoxLayout()
        stream_row.addWidget(self._stream_url_edit, stretch=1)
        stream_row.addWidget(self._test_stream_url_btn)
        capture_layout.addRow("URL stream ?", stream_row)
        capture_layout.addRow("Mode URL ?", self._url_mode_combo)
        capture_layout.addRow(self._adblock_hint_label)
        stt_row = QHBoxLayout()
        stt_row.addWidget(self._stt_model_combo, stretch=1)
        stt_row.addWidget(self._repair_stt_cache_btn)
        capture_layout.addRow("Modele STT local ?", stt_row)
        stt_hint = QLabel(
            "tiny: tres rapide / precision plus faible | base: equilibre | "
            "small: plus precis | medium: meilleure qualite mais plus lent"
        )
        stt_hint.setStyleSheet("font-size: 12px;")
        stt_hint.setWordWrap(True)
        capture_layout.addRow(stt_hint)

        translation_group = QGroupBox("Traduction")
        translation_layout = QFormLayout(translation_group)
        translation_hint = QLabel(
            "Le modele marque ★ Recommandé est un compromis vitesse/qualite pour traduction en direct."
        )
        translation_hint.setStyleSheet("font-size: 12px;")
        translation_hint.setWordWrap(True)
        translation_layout.addRow(translation_hint)
        translation_layout.addRow("Langue cible ?", self._target_combo)

        model_row = QHBoxLayout()
        model_row.addWidget(self._model_combo, stretch=1)
        model_row.addWidget(self._refresh_models_btn)
        translation_layout.addRow("Modele Live (temps reel) ?", model_row)

        file_model_row = QHBoxLayout()
        file_model_row.addWidget(self._file_model_combo, stretch=1)
        translation_layout.addRow("Modele Fichier (audio/video) ?", file_model_row)

        self._live_model_state_label = QLabel("Etat live: -")
        self._file_model_state_label = QLabel("Etat fichier: -")
        self._live_model_state_label.setWordWrap(True)
        self._file_model_state_label.setWordWrap(True)
        self._live_model_state_label.setStyleSheet("font-size: 12px; color: #9fd0ff;")
        self._file_model_state_label.setStyleSheet("font-size: 12px; color: #9fd0ff;")
        translation_layout.addRow(self._live_model_state_label)
        translation_layout.addRow(self._file_model_state_label)

        profiles_group = QGroupBox("Profils rapides")
        profiles_layout = QVBoxLayout(profiles_group)
        profiles_hint = QLabel(
            "Choisis un profil adapte a ton usage. Tu peux ensuite affiner manuellement si besoin."
        )
        profiles_hint.setStyleSheet("font-size: 12px;")
        profiles_hint.setWordWrap(True)
        profiles_layout.addWidget(profiles_hint)

        profiles_btn_row = QHBoxLayout()
        self._preset_fast_btn = QPushButton("Ultra-Rapide (Live)")
        self._preset_quality_btn = QPushButton("Qualite (Webinaire)")
        self._preset_auto_btn = QPushButton("Auto (Detection CPU/GPU)")
        self._preset_fast_btn.clicked.connect(self._apply_preset_ultra_fast)
        self._preset_quality_btn.clicked.connect(self._apply_preset_quality)
        self._preset_auto_btn.clicked.connect(self._apply_preset_auto)
        profiles_btn_row.addWidget(self._preset_fast_btn)
        profiles_btn_row.addWidget(self._preset_quality_btn)
        profiles_btn_row.addWidget(self._preset_auto_btn)
        profiles_layout.addLayout(profiles_btn_row)

        self._profile_summary_label = QLabel()
        self._profile_summary_label.setWordWrap(True)
        self._profile_summary_label.setStyleSheet("font-size: 12px;")
        profiles_layout.addWidget(self._profile_summary_label)

        self._stt_diagnostic_label = QLabel()
        self._stt_diagnostic_label.setWordWrap(True)
        self._stt_diagnostic_label.setStyleSheet("font-size: 12px; color: #b2beca;")
        profiles_layout.addWidget(self._stt_diagnostic_label)

        overlay_group = QGroupBox("Overlay (affichage)")
        overlay_layout = QFormLayout(overlay_group)

        bg_row = QHBoxLayout()
        bg_row.addWidget(self._bg_color_label)
        bg_row.addWidget(self._bg_color_btn)
        overlay_layout.addRow("Couleur de fond ?", bg_row)

        opacity_row = QHBoxLayout()
        opacity_row.addWidget(self._opacity_slider)
        overlay_layout.addRow("Opacite ?", opacity_row)

        text_row = QHBoxLayout()
        text_row.addWidget(self._text_color_label)
        text_row.addWidget(self._text_color_btn)
        overlay_layout.addRow("Couleur du texte ?", text_row)

        overlay_layout.addRow("Taille de police ?", self._font_size_spin)
        overlay_layout.addRow("Style ?", self._font_bold_checkbox)
        overlay_layout.addRow("Vitesse sous-titres ?", self._display_speed_combo)
        max_lines_row = QHBoxLayout()
        max_lines_row.addWidget(self._max_lines_minus_btn)
        max_lines_row.addWidget(self._max_lines_spin)
        max_lines_row.addWidget(self._max_lines_plus_btn)
        overlay_layout.addRow("Lignes visibles ?", max_lines_row)
        overlay_layout.addRow("Previsualisation", self._preview_container)

        save_btn = QPushButton("Enregistrer")
        cancel_btn = QPushButton("Annuler")
        import_btn = QPushButton("Importer config")
        export_btn = QPushButton("Exporter config")
        quick_export_btn = QPushButton("Export rapide")
        quick_restore_btn = QPushButton("Restaurer rapide")
        media_translate_btn = QPushButton("Traduire un fichier video/audio")
        shortcut_page_btn = QPushButton("Page raccourcis clavier")

        import_btn.clicked.connect(self._import_config)
        export_btn.clicked.connect(self._export_config)
        quick_export_btn.clicked.connect(self._quick_export_backup)
        quick_restore_btn.clicked.connect(self._quick_restore_backup)
        media_translate_btn.clicked.connect(self._start_media_translation)
        shortcut_page_btn.clicked.connect(self._open_shortcut_settings_page)
        save_btn.clicked.connect(self._save_and_close)
        cancel_btn.clicked.connect(self.reject)

        footer = QHBoxLayout()
        footer.addWidget(media_translate_btn)
        footer.addWidget(shortcut_page_btn)
        footer.addWidget(import_btn)
        footer.addWidget(export_btn)
        footer.addWidget(quick_export_btn)
        footer.addWidget(quick_restore_btn)
        footer.addStretch(1)
        footer.addWidget(cancel_btn)
        footer.addWidget(save_btn)

        root = QVBoxLayout(self)
        root.addWidget(capture_group)
        root.addWidget(translation_group)
        root.addWidget(profiles_group)
        root.addWidget(overlay_group)
        root.addLayout(footer)

        # Style base en couleurs systeme pour rester coherent avec Windows.
        self.setStyleSheet("""
            QDialog {
                background-color: palette(window);
                color: palette(window-text);
            }
            QGroupBox {
                background-color: palette(base);
                color: palette(window-text);
                border: 1px solid palette(mid);
                border-radius: 8px;
                margin-top: 14px;
                padding: 6px 4px 4px 4px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 10px;
                top: 0px;
                padding: 0 4px;
                color: palette(window-text);
                background-color: palette(window);
            }
            QLabel {
                background-color: transparent;
                color: palette(window-text);
                border: none;
            }
            QPushButton {
                background-color: palette(button);
                color: palette(button-text);
                border: 1px solid palette(mid);
                padding: 4px 8px;
                border-radius: 6px;
            }
            QPushButton:hover {
                border: 1px solid palette(highlight);
            }
            QComboBox {
                background-color: palette(base);
                color: palette(text);
                border: 1px solid palette(mid);
                padding: 3px;
                border-radius: 6px;
            }
            QComboBox QAbstractItemView {
                background-color: palette(base);
                color: palette(text);
                selection-background-color: palette(highlight);
            }
            QSpinBox {
                background-color: palette(base);
                color: palette(text);
                border: 1px solid palette(mid);
                padding: 3px;
                border-radius: 6px;
            }
            QCheckBox {
                background-color: transparent;
                color: palette(window-text);
                border: none;
            }
            QSlider {
                border: none;
                background: transparent;
            }
            QPlainTextEdit, QLineEdit {
                background-color: palette(base);
                color: palette(text);
                border: 1px solid palette(mid);
                border-radius: 6px;
            }
        """)

        self._bind_preview_events()
        self._configure_help_tooltips()
        self._bind_form_events()
        self._apply_preview_style()
        self._refresh_audio_devices()
        self._refresh_ollama_models(silent=True)
        self._sync_form_from_config()
        self._update_profile_summary("Personnalise")

    @property
    def config(self) -> AppConfig:
        return self._config

    @property
    def media_translation_requested(self) -> bool:
        return self._media_translation_requested

    def _pick_background_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._bg_color_label.text()), self, "Couleur de fond")
        if color.isValid():
            self._bg_color_label.setText(color.name())
            self._apply_preview_style()

    def _pick_text_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._text_color_label.text()), self, "Couleur du texte")
        if color.isValid():
            self._text_color_label.setText(color.name())
            self._apply_preview_style()

    def _bind_preview_events(self) -> None:
        self._opacity_slider.valueChanged.connect(self._apply_preview_style)
        self._font_size_spin.valueChanged.connect(self._apply_preview_style)
        self._font_bold_checkbox.stateChanged.connect(self._apply_preview_style)
        self._max_lines_spin.valueChanged.connect(self._apply_preview_style)

    def _bind_form_events(self) -> None:
        self._stt_model_combo.currentTextChanged.connect(self._on_stt_model_changed)
        self._source_combo.currentTextChanged.connect(self._on_language_pair_changed)
        self._target_combo.currentTextChanged.connect(self._on_language_pair_changed)

    def _on_stt_model_changed(self, _value: str) -> None:
        self._update_stt_diagnostic()

    def _on_language_pair_changed(self, _value: str) -> None:
        # Keep recommendation badges coherent with current source/target languages.
        self._refresh_ollama_models(silent=True)

    def _refresh_audio_devices(self) -> None:
        current_value = self._audio_device_combo.currentText().strip() or self._config.audio.input_device.strip()
        devices = SystemAudioCapture.list_loopback_devices()

        self._audio_device_combo.blockSignals(True)
        self._audio_device_combo.clear()

        if devices:
            self._audio_device_combo.addItems(devices)
            if current_value and self._audio_device_combo.findText(current_value) >= 0:
                self._audio_device_combo.setCurrentText(current_value)
            elif self._config.audio.input_device and self._audio_device_combo.findText(self._config.audio.input_device) >= 0:
                self._audio_device_combo.setCurrentText(self._config.audio.input_device)
            else:
                self._audio_device_combo.setCurrentIndex(0)
        else:
            self._audio_device_combo.addItem("Aucun peripherique detecte")

        self._audio_device_combo.blockSignals(False)

    def _on_audio_source_changed(self) -> None:
        mode = str(self._audio_source_combo.currentData() or "system")
        is_url = mode == "url"
        self._audio_device_combo.setEnabled(not is_url)
        self._audio_refresh_btn.setEnabled(not is_url)
        # Keep URL tools always editable/clickable so user can paste/test first,
        # then switch mode when ready.
        self._stream_url_edit.setEnabled(True)
        self._test_stream_url_btn.setEnabled(True)
        self._adblock_hint_label.setVisible(True)

    def _test_stream_url(self) -> None:
        raw_url = self._stream_url_edit.text().strip()
        if not self._is_supported_stream_url(raw_url):
            QMessageBox.warning(
                self,
                "Tester URL",
                "URL invalide. Utilise une URL http/https de live/video.",
            )
            return

        # Auto-select URL capture mode when user explicitly tests a stream URL.
        url_index = self._audio_source_combo.findData("url")
        if url_index >= 0 and self._audio_source_combo.currentIndex() != url_index:
            self._audio_source_combo.setCurrentIndex(url_index)

        self._test_stream_url_btn.setEnabled(False)
        self._test_stream_url_btn.setText("Test en cours...")
        try:
            resolved_url, payload_bytes = UrlAudioCapture.test_stream_url(
                raw_url,
                sample_rate=self._config.audio.sample_rate,
                channels=self._config.audio.channels,
            )
            provider_hint = ""
            lower_url = raw_url.lower()
            if "youtube.com" in lower_url or "youtu.be" in lower_url or "twitch.tv" in lower_url:
                provider_hint = (
                    "\n\nConseil anti-pub: active un bloqueur pub sur la source (navigateur/app). "
                    "Les pubs server-side peuvent encore interrompre ponctuellement le flux."
                )
            QMessageBox.information(
                self,
                "Tester URL",
                f"Connexion OK. Flux audio detecte ({payload_bytes} octets lus)."
                f"\nURL media resolue: {resolved_url[:180]}"
                f"{provider_hint}",
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            QMessageBox.warning(
                self,
                "Tester URL",
                f"Echec test URL: {exc}",
            )
        finally:
            self._test_stream_url_btn.setText("Tester URL")
            self._on_audio_source_changed()

    def _apply_preview_style(self) -> None:
        bg = QColor(self._bg_color_label.text())
        text = QColor(self._text_color_label.text())
        opacity = self._opacity_slider.value() / 100.0
        weight = 700 if self._font_bold_checkbox.isChecked() else 400

        bg_rgba = f"rgba({bg.red()}, {bg.green()}, {bg.blue()}, {opacity:.3f})"

        self._preview_container.setStyleSheet(
            f"""
            QFrame {{
                background-color: {bg_rgba};
                border-radius: 10px;
                border: 1px solid #5f6368;
            }}
            QLabel {{
                color: {text.name()};
                font-size: {self._font_size_spin.value()}px;
                font-weight: {weight};
                background: transparent;
            }}
            """
        )
        self._preview_text.setText(
            "Ligne 1: Sous-titre en direct\n"
            "Ligne 2: Lecture plus confortable\n"
            "Ligne 3: Exemple multi-lignes"
        )
        self._preview_text.setMaximumHeight(self._max_lines_spin.value() * (self._font_size_spin.value() + 8))

    def _refresh_ollama_models(self, silent: bool = False) -> None:
        current_value = self._current_model_value()
        current_file_value = self._current_file_model_value()
        try:
            model_names = OllamaTranslator.discover_local_model_names(self._config.translation.host)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            if not silent:
                QMessageBox.warning(
                    self,
                    "Ollama",
                    f"Impossible de recuperer les modeles Ollama: {exc}",
                )
            model_names = []

        self._model_combo.clear()
        self._file_model_combo.clear()
        recommendations = OllamaTranslator.recommend_translation_models(
            model_names,
            self._source_combo.currentText(),
            self._target_combo.currentText(),
        )
        recommended_live = recommendations.get("live_recommended", "")
        recommended_file = recommendations.get("file_recommended", "")

        if model_names:
            for model_name in model_names:
                live_display = model_name
                file_display = model_name
                if model_name == recommended_live:
                    live_display = f"{model_name} ★ Recommandé"
                if model_name == recommended_file:
                    file_display = f"{model_name} ★ Recommandé"
                self._model_combo.addItem(live_display, model_name)
                self._file_model_combo.addItem(file_display, model_name)

        if current_value:
            idx = self._find_model_index_by_value(self._model_combo, current_value)
            if idx >= 0:
                self._model_combo.setCurrentIndex(idx)
            else:
                self._model_combo.setEditText(current_value)
        elif recommended_live:
            idx = self._find_model_index_by_value(self._model_combo, recommended_live)
            if idx >= 0:
                self._model_combo.setCurrentIndex(idx)

        if current_file_value:
            file_idx = self._find_model_index_by_value(self._file_model_combo, current_file_value)
            if file_idx >= 0:
                self._file_model_combo.setCurrentIndex(file_idx)
            else:
                self._file_model_combo.setEditText(current_file_value)
        elif recommended_file:
            file_idx = self._find_model_index_by_value(self._file_model_combo, recommended_file)
            if file_idx >= 0:
                self._file_model_combo.setCurrentIndex(file_idx)

        live_missing = recommendations.get("live_missing_suggestion", "")
        file_missing = recommendations.get("file_missing_suggestion", "")
        self._update_model_state_labels(recommended_live, recommended_file, live_missing, file_missing)
        if not silent:
            self._prompt_install_missing_recommended_models(live_missing, file_missing, model_names)

    def _apply_preset_ultra_fast(self) -> None:
        self._source_combo.setCurrentText("English")
        self._target_combo.setCurrentText("Francais")
        self._stt_model_combo.setCurrentText("tiny")

        # Priorite latence: chunks plus courts et seuil silence plus permissif.
        self._config.audio.chunk_seconds = 1.0
        self._config.audio.silence_threshold = 0.0008

        recommendations = OllamaTranslator.recommend_translation_models(
            self._available_model_values(),
            self._source_combo.currentText(),
            self._target_combo.currentText(),
        )
        fast_model = recommendations.get("live_recommended", "")
        if fast_model:
            idx = self._find_model_index_by_value(self._model_combo, fast_model)
            if idx >= 0:
                self._model_combo.setCurrentIndex(idx)
            else:
                self._model_combo.setEditText(fast_model)

        self._update_profile_summary("Ultra-Rapide")

    def _apply_preset_quality(self) -> None:
        self._source_combo.setCurrentText("English")
        self._target_combo.setCurrentText("Francais")
        self._stt_model_combo.setCurrentText("base")

        # Priorite fidelite: un peu plus de contexte audio pour des phrases stables.
        self._config.audio.chunk_seconds = 2.2
        self._config.audio.silence_threshold = 0.0012

        recommendations = OllamaTranslator.recommend_translation_models(
            self._available_model_values(),
            self._source_combo.currentText(),
            self._target_combo.currentText(),
        )
        quality_live_model = recommendations.get("live_recommended", "")
        quality_file_model = recommendations.get("file_recommended", "")
        if quality_live_model:
            idx = self._find_model_index_by_value(self._model_combo, quality_live_model)
            if idx >= 0:
                self._model_combo.setCurrentIndex(idx)
            else:
                self._model_combo.setEditText(quality_live_model)

        if quality_file_model:
            file_idx = self._find_model_index_by_value(self._file_model_combo, quality_file_model)
            if file_idx >= 0:
                self._file_model_combo.setCurrentIndex(file_idx)
            else:
                self._file_model_combo.setEditText(quality_file_model)

        self._update_profile_summary("Qualite")

    def _apply_preset_auto(self) -> None:
        has_gpu, num_cores = self._detect_hardware_capabilities()
        use_quality = has_gpu or num_cores >= 8

        if use_quality:
            self._apply_preset_quality()
            self._update_profile_summary(f"Auto (Qualite) - {num_cores}C {('+ GPU' if has_gpu else '- GPU')}")
        else:
            self._apply_preset_ultra_fast()
            self._update_profile_summary(f"Auto (Rapide) - {num_cores}C {('+ GPU' if has_gpu else '- GPU')}")

    def _detect_hardware_capabilities(self) -> tuple[bool, int]:
        has_gpu = self._has_nvidia_gpu()
        num_cores = self._get_cpu_core_count()
        return has_gpu, num_cores

    @staticmethod
    def _has_nvidia_gpu() -> bool:
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=count", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            return result.returncode == 0 and int(result.stdout.strip().split()[0]) > 0
        except Exception:
            return False

    @staticmethod
    def _get_cpu_core_count() -> int:
        try:
            return cpu_count() or 4
        except Exception:
            return 4

    def _update_profile_summary(self, profile_name: str) -> None:
        self._profile_summary_label.setText(
            f"Profil: {profile_name} | STT={self._stt_model_combo.currentText()} | "
            f"source={str(self._audio_source_combo.currentData() or 'system')} | "
            f"url_mode={str(self._url_mode_combo.currentData() or 'low-latency')} | "
            f"chunk={self._config.audio.chunk_seconds:.1f}s | "
            f"live={self._current_model_value() or '-'} | file={self._current_file_model_value() or '-'} | "
            f"vitesse={self._display_speed_combo.currentText()} | lignes={self._max_lines_spin.value()}"
        )
        self._update_stt_diagnostic()

    def _update_stt_diagnostic(self) -> None:
        stt_model = self._stt_model_combo.currentText().strip().lower()
        diagnostics = {
            "tiny": "Diagnostic STT: priorite vitesse, ideal live faible latence, precision plus faible.",
            "base": "Diagnostic STT: profil equilibre vitesse/qualite pour usage quotidien.",
            "small": "Diagnostic STT: precision amelioree, CPU plus sollicite.",
            "medium": "Diagnostic STT: meilleure qualite de transcription, latence et charge plus elevees.",
        }
        message = diagnostics.get(stt_model, "Diagnostic STT: modele non reconnu.")
        self._stt_diagnostic_label.setText(message)

    def _update_model_state_labels(
        self,
        recommended_live: str,
        recommended_file: str,
        live_missing: str,
        file_missing: str,
    ) -> None:
        selected_live = self._current_model_value() or "-"
        selected_file = self._current_file_model_value() or "-"

        live_status = f"Etat live: selection={selected_live} | recommande={recommended_live or '-'}"
        file_status = f"Etat fichier: selection={selected_file} | recommande={recommended_file or '-'}"

        if live_missing:
            live_status += f" | manquant propose={live_missing}"
        if file_missing:
            file_status += f" | manquant propose={file_missing}"

        self._live_model_state_label.setText(live_status)
        self._file_model_state_label.setText(file_status)

    def _available_model_values(self) -> list[str]:
        values: list[str] = []
        for idx in range(self._model_combo.count()):
            data = self._model_combo.itemData(idx)
            if isinstance(data, str) and data.strip():
                values.append(data.strip())
        return values

    def _prompt_install_missing_recommended_models(
        self,
        live_missing: str,
        file_missing: str,
        installed_models: list[str],
    ) -> None:
        installed_lower = {name.lower() for name in installed_models}
        missing_candidates: list[str] = []
        if live_missing and live_missing.lower() not in installed_lower:
            missing_candidates.append(live_missing)
        if file_missing and file_missing.lower() not in installed_lower and file_missing.lower() not in {
            candidate.lower() for candidate in missing_candidates
        }:
            missing_candidates.append(file_missing)

        if not missing_candidates:
            return

        details = "\n".join(f"- {name}" for name in missing_candidates)
        prompt = QMessageBox(self)
        prompt.setIcon(QMessageBox.Icon.Question)
        prompt.setWindowTitle("Modeles recommandes manquants")
        prompt.setText(
            "Des modeles recommandes pour de meilleures performances ne sont pas installes dans Ollama."
        )
        prompt.setInformativeText(
            f"Modeles proposes:\n{details}\n\n"
            "Veux-tu les telecharger/installer maintenant (ollama pull) ?"
        )
        install_button = prompt.addButton("Installer", QMessageBox.ButtonRole.AcceptRole)
        prompt.addButton("Passer", QMessageBox.ButtonRole.RejectRole)
        prompt.exec()

        if prompt.clickedButton() != install_button:
            return

        installed_now: list[str] = []
        failed_now: list[str] = []
        for model_name in missing_candidates:
            ok, output = OllamaTranslator.install_model(model_name)
            if ok:
                installed_now.append(model_name)
            else:
                failed_now.append(f"{model_name}: {output[:200]}")

        if installed_now:
            QMessageBox.information(
                self,
                "Installation Ollama",
                "Installation terminee pour:\n" + "\n".join(f"- {name}" for name in installed_now),
            )
            self._refresh_ollama_models(silent=True)

        if failed_now:
            QMessageBox.warning(
                self,
                "Installation Ollama",
                "Certaines installations ont echoue:\n" + "\n".join(failed_now),
            )

    def _configure_help_tooltips(self) -> None:
        self._source_combo.setToolTip("Langue detectee dans l'audio source (STT).")
        self._target_combo.setToolTip("Langue de sortie de la traduction.")
        self._audio_source_combo.setToolTip("Systeme = son du PC. URL = flux YouTube/Twitch/autre via ffmpeg.")
        self._audio_device_combo.setToolTip("Peripherique loopback a ecouter quand le mode Systeme est actif.")
        self._audio_refresh_btn.setToolTip("Rescan des peripheriques audio disponibles.")
        self._stream_url_edit.setToolTip("URL http(s) d'un flux audio/video a traduire en direct.")
        self._test_stream_url_btn.setToolTip("Teste la connectivite URL avant de lancer la capture.")
        self._url_mode_combo.setToolTip("Latence faible = plus rapide. Qualite elevee = plus stable.")
        self._model_combo.setToolTip("Modele Ollama pour la traduction en direct.")
        self._file_model_combo.setToolTip("Modele Ollama pour traduction audio/video (qualite privilegiee).")
        self._refresh_models_btn.setToolTip("Scanne les modeles Ollama installes et met a jour la recommandation.")
        self._opacity_slider.setToolTip("Transparence du fond de l'overlay.")
        self._font_size_spin.setToolTip("Taille du texte des sous-titres.")
        self._font_bold_checkbox.setToolTip("Active/desactive le texte gras.")
        self._display_speed_combo.setToolTip("Vitesse d'affichage des lignes a l'ecran.")
        self._max_lines_spin.setToolTip("Nombre maximal de lignes conservees dans l'overlay.")
        self._profile_summary_label.setToolTip("Resume rapide des reglages actifs du profil.")
        self._stt_diagnostic_label.setToolTip("Aide STT: explique le compromis vitesse/precision du modele choisi.")
        self._live_model_state_label.setToolTip("Etat du modele live: selection actuelle, recommandation et manquant eventuel.")
        self._file_model_state_label.setToolTip("Etat du modele fichier: selection actuelle, recommandation et manquant eventuel.")

    def _save_and_close(self) -> None:
        if str(self._audio_source_combo.currentData() or "system") == "url":
            raw_url = self._stream_url_edit.text().strip()
            if not raw_url:
                system_idx = self._audio_source_combo.findData("system")
                if system_idx >= 0:
                    self._audio_source_combo.setCurrentIndex(system_idx)
                self._stream_url_edit.setText("")
                QMessageBox.information(
                    self,
                    "Source URL desactivee",
                    "URL vide detectee: bascule automatique en mode audio systeme.",
                )
            elif not self._is_supported_stream_url(raw_url):
                QMessageBox.warning(
                    self,
                    "URL invalide",
                    "Renseigne une URL live/video valide (http/https) avant de sauvegarder.",
                )
                return

        self._apply_form_to_config()

        save_app_config(self._config, self._config_path)
        logger.info(
            "Parametres sauvegardes: source=%s target=%s stt=%s/%s chunk=%.2f silence=%.4f live_model=%s file_model=%s lines=%s",
            self._config.languages.source,
            self._config.languages.target,
            self._config.stt.model_size,
            self._config.stt.language,
            self._config.audio.chunk_seconds,
            self._config.audio.silence_threshold,
            self._config.translation.live_model,
            self._config.translation.file_model,
            self._config.overlay.max_visible_lines,
        )
        self.accept()

    @staticmethod
    def _is_supported_stream_url(url: str) -> bool:
        value = (url or "").strip().lower()
        if not value.startswith(("http://", "https://")):
            return False
        return "." in value and len(value) >= 12

    def _apply_form_to_config(self) -> None:
        self._config.languages.source = self._source_combo.currentText()
        self._config.languages.target = self._target_combo.currentText()
        self._config.stt.model_size = self._stt_model_combo.currentText().strip() or self._config.stt.model_size
        self._config.stt.language = self._stt_language_for_source(self._config.languages.source)
        chosen_model = self._current_model_value()
        chosen_file_model = self._current_file_model_value()
        self._config.translation.live_model = chosen_model or self._config.translation.live_model
        self._config.translation.file_model = chosen_file_model or self._config.translation.file_model
        self._config.translation.model = self._config.translation.live_model
        self._config.audio.input_device = self._audio_device_combo.currentText().strip()
        self._config.audio.source_mode = str(self._audio_source_combo.currentData() or "system")
        self._config.audio.stream_url = self._stream_url_edit.text().strip()
        self._config.audio.url_live_mode = str(self._url_mode_combo.currentData() or "low-latency")

        self._config.overlay = replace(
            self._config.overlay,
            background_color=self._bg_color_label.text(),
            background_opacity=self._opacity_slider.value() / 100.0,
            text_color=self._text_color_label.text(),
            font_size=self._font_size_spin.value(),
            font_bold=self._font_bold_checkbox.isChecked(),
            display_speed=str(self._display_speed_combo.currentData() or "normal"),
            max_visible_lines=self._max_lines_spin.value(),
        )

    def _sync_form_from_config(self) -> None:
        self._source_combo.setCurrentText(self._config.languages.source)
        self._target_combo.setCurrentText(self._config.languages.target)
        self._stt_model_combo.setCurrentText(self._config.stt.model_size)
        self._model_combo.setCurrentText(self._config.translation.live_model)
        self._file_model_combo.setCurrentText(self._config.translation.file_model)
        self._refresh_audio_devices()
        source_idx = self._audio_source_combo.findData(getattr(self._config.audio, "source_mode", "system"))
        self._audio_source_combo.setCurrentIndex(source_idx if source_idx >= 0 else 0)
        self._stream_url_edit.setText(getattr(self._config.audio, "stream_url", ""))
        url_mode = getattr(self._config.audio, "url_live_mode", "low-latency")
        url_mode_idx = self._url_mode_combo.findData(url_mode)
        self._url_mode_combo.setCurrentIndex(url_mode_idx if url_mode_idx >= 0 else 0)
        self._on_audio_source_changed()
        self._bg_color_label.setText(self._config.overlay.background_color)
        self._text_color_label.setText(self._config.overlay.text_color)
        self._opacity_slider.setValue(int(self._config.overlay.background_opacity * 100))
        self._font_size_spin.setValue(self._config.overlay.font_size)
        self._font_bold_checkbox.setChecked(self._config.overlay.font_bold)
        speed_value = getattr(self._config.overlay, "display_speed", "normal")
        speed_idx = self._display_speed_combo.findData(speed_value)
        if speed_idx >= 0:
            self._display_speed_combo.setCurrentIndex(speed_idx)
        else:
            self._display_speed_combo.setCurrentIndex(1)
        self._max_lines_spin.setRange(1, 12)
        lines_value = max(1, min(12, int(getattr(self._config.overlay, "max_visible_lines", 3))))
        self._max_lines_spin.setValue(lines_value)
        self._apply_preview_style()
        self._update_profile_summary("Personnalise")

    def _config_base_dir(self) -> Path:
        return self._config_path.parent

    def _backup_path(self) -> Path:
        return self._config_base_dir() / "app_config.backup.json"

    def _export_config(self) -> None:
        self._apply_form_to_config()
        default_target = self._config_base_dir() / "app_config.export.json"

        selected_file = self._get_save_file_path(
            title="Exporter la configuration",
            initial_path=str(default_target),
            file_filter="Fichiers JSON (*.json)",
        )

        if not selected_file:
            return

        try:
            target = Path(selected_file)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w", encoding="utf-8") as file:
                json.dump(self._config.to_dict(), file, indent=2, ensure_ascii=False)
            QMessageBox.information(self, "Export", f"Configuration exportee vers:\n{target}")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            QMessageBox.critical(self, "Export", f"Echec de l'export: {exc}")

    def _import_config(self) -> None:
        selected_file = self._get_open_file_path(
            title="Importer une configuration",
            initial_dir=str(self._config_base_dir()),
            file_filter="Fichiers JSON (*.json)",
        )

        if not selected_file:
            return

        try:
            source = Path(selected_file)
            with source.open("r", encoding="utf-8") as file:
                data = json.load(file)

            imported = AppConfig.from_dict(data)
            self._config = imported
            self._sync_form_from_config()

            save_app_config(self._config, self._config_path)
            QMessageBox.information(self, "Import", f"Configuration importee depuis:\n{source}")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            QMessageBox.critical(self, "Import", f"Echec de l'import: {exc}")

    def _quick_export_backup(self) -> None:
        self._apply_form_to_config()
        target = self._backup_path()

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w", encoding="utf-8") as file:
                json.dump(self._config.to_dict(), file, indent=2, ensure_ascii=False)
            QMessageBox.information(self, "Export rapide", f"Backup enregistre:\n{target}")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            QMessageBox.critical(self, "Export rapide", f"Echec de l'export rapide: {exc}")

    def _quick_restore_backup(self) -> None:
        source = self._backup_path()
        if not source.exists():
            QMessageBox.warning(self, "Restaurer rapide", f"Backup introuvable:\n{source}")
            return

        try:
            with source.open("r", encoding="utf-8") as file:
                data = json.load(file)

            self._config = AppConfig.from_dict(data)
            self._sync_form_from_config()
            save_app_config(self._config, self._config_path)
            QMessageBox.information(self, "Restaurer rapide", f"Backup restaure:\n{source}")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            QMessageBox.critical(self, "Restaurer rapide", f"Echec de la restauration rapide: {exc}")

    def _current_model_value(self) -> str:
        current_data = self._model_combo.currentData()
        if isinstance(current_data, str) and current_data.strip():
            return current_data.strip()

        text = self._model_combo.currentText().strip()
        if text.endswith("★ Recommandé"):
            text = text.replace("★ Recommandé", "").strip()
        return text

    def _current_file_model_value(self) -> str:
        current_data = self._file_model_combo.currentData()
        if isinstance(current_data, str) and current_data.strip():
            return current_data.strip()

        text = self._file_model_combo.currentText().strip()
        if text.endswith("★ Recommandé"):
            text = text.replace("★ Recommandé", "").strip()
        return text

    @staticmethod
    def _find_model_index_by_value(combo: QComboBox, model_value: str) -> int:
        for idx in range(combo.count()):
            data = combo.itemData(idx)
            if isinstance(data, str) and data == model_value:
                return idx
        return -1

    def _get_open_file_path(self, title: str, initial_dir: str, file_filter: str) -> str:
        dialog = QFileDialog(self, title, initial_dir, file_filter)
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        if dialog.exec():
            selected = dialog.selectedFiles()
            return selected[0] if selected else ""
        return ""

    def _get_save_file_path(self, title: str, initial_path: str, file_filter: str) -> str:
        dialog = QFileDialog(self, title, initial_path, file_filter)
        dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        dialog.setDefaultSuffix("json")
        if dialog.exec():
            selected = dialog.selectedFiles()
            return selected[0] if selected else ""
        return ""

    def _repair_stt_cache(self) -> None:
        cache_dir = (self._config_path.parent / "cache" / "faster-whisper").resolve()
        try:
            import shutil

            if cache_dir.exists():
                shutil.rmtree(cache_dir, ignore_errors=True)
            cache_dir.mkdir(parents=True, exist_ok=True)
            self._config.stt.cache_dir = str(cache_dir)
            save_app_config(self._config, self._config_path)
            QMessageBox.information(
                self,
                "Cache STT",
                "Le cache STT a ete reinitialise. Relance la capture: le modele sera retelecharge proprement.",
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            QMessageBox.critical(self, "Cache STT", f"Impossible de reparer le cache STT: {exc}")

    def _start_media_translation(self) -> None:
        self._apply_form_to_config()
        save_app_config(self._config, self._config_path)
        self._media_translation_requested = True
        self.accept()

    def _open_shortcut_settings_page(self) -> None:
        dialog = ShortcutSettingsDialog(self._config.shortcuts, parent=self)
        if dialog.exec():
            self._config.shortcuts = dialog.result
            save_app_config(self._config, self._config_path)
            QMessageBox.information(self, "Raccourcis", "Raccourcis enregistres pour les prochains lancements.")

    @staticmethod
    def _stt_language_for_source(source_label: str) -> str:
        mapping = {
            "english": "en",
            "french": "fr",
            "francais": "fr",
            "français": "fr",
            "spanish": "es",
            "german": "de",
            "italian": "it",
        }
        return mapping.get(source_label.strip().lower(), "auto")

from __future__ import annotations

import logging
import sys
import threading
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QStyle,
    QSystemTrayIcon,
    QWidget,
)

from config import AppConfig, load_app_config, save_app_config
from core.audio_capture import SystemAudioCapture
from core.global_hotkeys import GlobalHotkeyManager
from core.media_translation import MediaTranslationService
from core.ollama_client import OllamaTranslator
from core.pipeline import TranslationPipeline
from ui.overlay_window import OverlayWindow
from ui.media_translation_dialog import MediaTranslationDialog
from ui.settings_window import SettingsWindow
from ui.vlc_launch_dialog import VlcLaunchDialog
from ui.vlc_player_window import VlcPlayerWindow


# En mode PyInstaller frozen, sys.stderr/stdout sont None.
# basicConfig avec StreamHandler crasherait (NoneType.write).
# On installe uniquement un NullHandler ici; le FileHandler est ajoute dans _configure_runtime_logging.
if getattr(sys, "frozen", False):
    logging.basicConfig(level=logging.WARNING, handlers=[logging.NullHandler()])
else:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

logger = logging.getLogger("aura-traduction")


def _configure_windows_app_id() -> None:
    if sys.platform != "win32":
        return

    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AuraNeo.AuraTranslat")
    except Exception:  # pylint: disable=broad-exception-caught
        logger.debug("Impossible de definir AppUserModelID Windows.", exc_info=True)


class UiBridge(QObject):
    translated = pyqtSignal(str)
    error = pyqtSignal(str)
    status = pyqtSignal(str)
    debug = pyqtSignal(str)
    media_progress = pyqtSignal(int, str)
    media_done = pyqtSignal(str)
    media_error = pyqtSignal(str)
    hotkey_toggle_live = pyqtSignal()
    hotkey_open_settings = pyqtSignal()
    hotkey_toggle_overlay = pyqtSignal()


class AuraTraductionApp:
    def __init__(self) -> None:
        _configure_windows_app_id()
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)

        self.app_icon = self._resolve_app_icon()
        if not self.app_icon.isNull():
            self.app.setWindowIcon(self.app_icon)

        self.config_path = self._resolve_config_path()
        self._configure_runtime_logging(self.config_path.parent)
        self.config: AppConfig = load_app_config(self.config_path)
        if not self.config.stt.cache_dir:
            stt_cache_dir = (self.config_path.parent / "cache" / "faster-whisper").resolve()
            stt_cache_dir.mkdir(parents=True, exist_ok=True)
            self.config.stt.cache_dir = str(stt_cache_dir)
        # Force one consistent write to keep app_config / backup / export aligned.
        save_app_config(self.config, self.config_path)
        logger.info(
            "Config chargee: source=%s target=%s stt=%s/%s/%s chunk=%.2f silence=%.4f live_model=%s file_model=%s lines=%s",
            self.config.languages.source,
            self.config.languages.target,
            self.config.stt.model_size,
            self.config.stt.language,
            self.config.stt.device,
            self.config.audio.chunk_seconds,
            self.config.audio.silence_threshold,
            self.config.translation.live_model,
            self.config.translation.file_model,
            self.config.overlay.max_visible_lines,
        )

        self.bridge = UiBridge()
        self.overlay = OverlayWindow(self.config.overlay)
        if not self.app_icon.isNull():
            self.overlay.setWindowIcon(self.app_icon)
        self.overlay.set_actions(
            self.open_settings,
            self.quit,
            self.toggle_live_translation,
            self.hide_overlay,
        )

        self.bridge.translated.connect(self.overlay.append_translation)
        self.bridge.error.connect(self.handle_pipeline_error)
        self.bridge.status.connect(self.overlay.set_status)
        self.bridge.debug.connect(self.on_pipeline_debug)
        self.bridge.media_progress.connect(self._on_media_progress)
        self.bridge.media_done.connect(self._on_media_done)
        self.bridge.media_error.connect(self._on_media_error)
        self.bridge.hotkey_toggle_live.connect(self.toggle_live_translation)
        self.bridge.hotkey_open_settings.connect(self.open_settings)
        self.bridge.hotkey_toggle_overlay.connect(self.toggle_overlay_visibility)

        self.pipeline = TranslationPipeline(
            config=self.config,
            on_translation=self.on_translation,
            on_error=self.on_pipeline_error,
            on_status=self.on_pipeline_status,
            on_debug=self.on_pipeline_debug,
        )
        self._live_paused = False
        self._pause_live_action: QAction | None = None
        self._shortcuts: list[QShortcut] = []
        self._global_hotkeys = GlobalHotkeyManager()
        self._restart_retry_count = 0
        self._media_worker: threading.Thread | None = None
        self._media_cancel_event: threading.Event | None = None
        self._media_progress_dialog: QProgressDialog | None = None
        self._last_media_input_path: Path | None = None
        self._last_media_srt_path: Path | None = None
        self._vlc_window: VlcPlayerWindow | None = None

        self.app.aboutToQuit.connect(self.pipeline.stop)

        self.tray_icon = self._build_system_tray_icon()
        self._configure_shortcuts()
        self._auto_recovery_attempted = False
        self._transcript_lock = threading.Lock()
        transcript_dir = self.config_path.parent / "exports"
        transcript_dir.mkdir(parents=True, exist_ok=True)
        self._live_transcript_path = transcript_dir / f"traductions-live-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
        self._write_live_transcript_header()
        self._warmup_ollama_service_silently()
        self._run_startup_sanity_check()

    def run(self) -> int:
        self.show_overlay()
        QTimer.singleShot(0, self.show_overlay)
        if self.tray_icon is not None:
            self.tray_icon.show()
        if not self._live_paused:
            self.pipeline.start()
        return self.app.exec()

    def on_translation(self, english_text: str, translated_text: str) -> None:
        if self._live_paused:
            return
        logger.info("EN: %s", english_text)
        logger.info("TR: %s", translated_text)
        self._append_live_translation(english_text, translated_text)
        self.bridge.translated.emit(translated_text)

    def on_pipeline_error(self, message: str) -> None:
        self.bridge.error.emit(message)

    def on_pipeline_status(self, message: str) -> None:
        self.bridge.status.emit(message)

    def on_pipeline_debug(self, message: str) -> None:
        logger.debug(message)

    def handle_pipeline_error(self, message: str) -> None:
        if not self._auto_recovery_attempted:
            self._auto_recovery_attempted = True
            logger.error("Erreur pipeline detectee, activation mode secours: %s", message)
            self._apply_emergency_stable_profile()
            QTimer.singleShot(0, self.restart_pipeline)
            QMessageBox.warning(
                self.overlay,
                "Recuperation automatique",
                "Le pipeline a rencontre une erreur.\n"
                "Un profil stable (CPU + tiny) vient d'etre applique automatiquement.",
            )
            return

        QMessageBox.critical(self.overlay, "Erreur Pipeline", message)

    def open_settings(self) -> None:
        dialog = SettingsWindow(
            self.config,
            self.config_path,
            parent=self.overlay,
            on_translate_media=self.process_media_file_from_settings,
        )
        if dialog.exec():
            self.config = dialog.config
            self.overlay.apply_overlay_config(self.config.overlay)
            self._configure_shortcuts()
            # If user requested file translation from settings, start it only after dialog closes.
            if dialog.media_translation_requested:
                QTimer.singleShot(0, self.process_media_file)
                return

            # Run restart asynchronously to avoid re-entrancy issues while dialog is closing.
            if self._live_paused:
                self.overlay.set_status("Parametres appliques. Traduction live en pause.")
            else:
                QTimer.singleShot(0, self.restart_pipeline)

    def toggle_live_translation(self) -> None:
        if self._live_paused:
            self._live_paused = False
            self.overlay.set_live_paused(False)
            self.overlay.set_status("Reprise de la traduction en direct...")
            self.restart_pipeline()
        else:
            self.pipeline.stop()
            self._live_paused = True
            self.overlay.set_live_paused(True)
            self.overlay.set_status("Traduction en direct en pause.")
        self._update_pause_action_label()

    def restart_pipeline(self) -> None:
        if self._live_paused:
            return
        self.pipeline.stop()
        if self.pipeline.is_running():
            self._restart_retry_count += 1
            if self._restart_retry_count <= 12:
                self.overlay.set_status("Application des nouveaux parametres...")
                QTimer.singleShot(350, self.restart_pipeline)
                return

            logger.error("Redemarrage annule: pipeline precedent toujours actif apres retries.")
            self._restart_retry_count = 0
            QMessageBox.warning(
                self.overlay,
                "Redemarrage capture",
                "Les parametres sont enregistres, mais le redemarrage automatique a pris trop de temps."
                "\nClique sur 'Redemarrer la capture' depuis l'icone systeme.",
            )
            return

        self._restart_retry_count = 0

        self.pipeline = TranslationPipeline(
            config=self.config,
            on_translation=self.on_translation,
            on_error=self.on_pipeline_error,
            on_status=self.on_pipeline_status,
            on_debug=self.on_pipeline_debug,
        )
        self.pipeline.start()
        self._auto_recovery_attempted = False

    def _warmup_ollama_service_silently(self) -> None:
        def _worker() -> None:
            try:
                launched = OllamaTranslator.ensure_service_with_autostart(self.config.translation.host, startup_timeout=5.0)
                if launched:
                    logger.info("Ollama demarre silencieusement au lancement.")
            except Exception:  # pylint: disable=broad-exception-caught
                logger.debug("Warmup Ollama non critique indisponible.", exc_info=True)

        threading.Thread(target=_worker, name="aura-ollama-warmup", daemon=True).start()

    def _run_startup_sanity_check(self) -> None:
        def _worker() -> None:
            checks: list[tuple[str, bool, str]] = []

            try:
                devices = SystemAudioCapture.list_loopback_devices()
                ok = len(devices) > 0
                checks.append(("Audio", ok, f"{len(devices)} peripherique(s)" if ok else "aucun peripherique detecte"))
            except Exception as exc:  # pylint: disable=broad-exception-caught
                checks.append(("Audio", False, str(exc)))

            try:
                import faster_whisper  # noqa: F401

                checks.append(("STT", True, "module charge"))
            except Exception as exc:  # pylint: disable=broad-exception-caught
                checks.append(("STT", False, str(exc)))

            try:
                ollama_ok = OllamaTranslator.ensure_service_with_autostart(self.config.translation.host, startup_timeout=4.0)
                checks.append(("Ollama", ollama_ok, "service actif" if ollama_ok else "service non actif"))
            except Exception as exc:  # pylint: disable=broad-exception-caught
                checks.append(("Ollama", False, str(exc)))

            try:
                import vlc  # noqa: F401

                checks.append(("VLC", True, "python-vlc charge"))
            except Exception as exc:  # pylint: disable=broad-exception-caught
                checks.append(("VLC", False, str(exc)))

            try:
                import imageio_ffmpeg

                ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
                checks.append(("FFmpeg", bool(ffmpeg_path), "binaire detecte" if ffmpeg_path else "binaire introuvable"))
            except Exception as exc:  # pylint: disable=broad-exception-caught
                checks.append(("FFmpeg", False, str(exc)))

            failed = [f"{name}: {detail}" for name, ok, detail in checks if not ok]
            if failed:
                summary = "Sanity check: partiel. " + " | ".join(failed[:3])
                self.bridge.status.emit(summary)
                logger.warning("Sanity check partiel: %s", failed)
            else:
                self.bridge.status.emit("Sanity check: OK (audio, STT, Ollama, VLC, FFmpeg).")

        threading.Thread(target=_worker, name="aura-sanity-check", daemon=True).start()

    def _configure_shortcuts(self) -> None:
        for shortcut in self._shortcuts:
            shortcut.setEnabled(False)
            shortcut.deleteLater()

        pause_seq = self.config.shortcuts.toggle_pause
        settings_seq = self.config.shortcuts.open_settings
        overlay_seq = self.config.shortcuts.toggle_overlay

        # Raccourcis applicatifs persistants (actifs quand la fenetre Aura-Traduction a le focus).
        pause_shortcut = QShortcut(QKeySequence(pause_seq), self.overlay)
        pause_shortcut.activated.connect(self.toggle_live_translation)

        settings_shortcut = QShortcut(QKeySequence(settings_seq), self.overlay)
        settings_shortcut.activated.connect(self.open_settings)

        hide_shortcut = QShortcut(QKeySequence(overlay_seq), self.overlay)
        hide_shortcut.activated.connect(self.toggle_overlay_visibility)

        self._shortcuts = [pause_shortcut, settings_shortcut, hide_shortcut]

        self._global_hotkeys.configure(
            toggle_pause_seq=pause_seq,
            open_settings_seq=settings_seq,
            toggle_overlay_seq=overlay_seq,
            on_toggle_pause=lambda: self.bridge.hotkey_toggle_live.emit(),
            on_open_settings=lambda: self.bridge.hotkey_open_settings.emit(),
            on_toggle_overlay=lambda: self.bridge.hotkey_toggle_overlay.emit(),
        )

    def _apply_emergency_stable_profile(self) -> None:
        self.config.audio.chunk_seconds = 1.0
        self.config.audio.silence_threshold = 0.0008
        self.config.stt.model_size = "tiny"
        self.config.stt.device = "cpu"
        self.config.stt.compute_type = "int8"
        self.config.translation.live_model = "llama3.1:8b"
        if not self.config.translation.file_model.strip():
            self.config.translation.file_model = "llama3.1:8b"
        self.config.translation.model = self.config.translation.live_model
        self.config.overlay = replace(
            self.config.overlay,
            display_speed="normal",
            max_visible_lines=max(4, int(getattr(self.config.overlay, "max_visible_lines", 4))),
        )
        save_app_config(self.config, self.config_path)
        self.overlay.set_status("Nouveaux parametres appliques.")

    def _write_live_transcript_header(self) -> None:
        lines = [
            "Aura-Translat - Journal live",
            f"Demarrage: {datetime.now().isoformat(timespec='seconds')}",
            f"Source: {self.config.languages.source}",
            f"Cible: {self.config.languages.target}",
            "",
        ]
        with self._live_transcript_path.open("w", encoding="utf-8") as file:
            file.write("\n".join(lines))

    def _append_live_translation(self, english_text: str, translated_text: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        block = (
            f"[{timestamp}]\n"
            f"EN: {english_text.strip()}\n"
            f"FR: {translated_text.strip()}\n\n"
        )
        with self._transcript_lock:
            with self._live_transcript_path.open("a", encoding="utf-8") as file:
                file.write(block)

    def process_media_file(self) -> None:
        logger.info("Demarrage flux: traduction fichier media (tray)")
        self._process_media_file_internal(None)

    def process_media_file_from_settings(self, parent_widget: QWidget | None = None) -> None:
        logger.info("Demarrage flux: traduction fichier media (settings)")
        self._process_media_file_internal(parent_widget)

    def _process_media_file_internal(self, parent_widget: QWidget | None) -> None:
        if self._media_worker is not None and self._media_worker.is_alive():
            QMessageBox.information(
                self.overlay,
                "Traduction fichier",
                "Un traitement de fichier est deja en cours.",
            )
            return

        dialog_parent = parent_widget or self.overlay
        media_dialog = MediaTranslationDialog(
            base_dir=self.config_path.parent,
            default_input=self._last_media_input_path,
            parent=dialog_parent,
        )
        if not media_dialog.exec():
            return

        media_path, output_path = media_dialog.selected_paths

        self._last_media_input_path = Path(media_path)
        self._last_media_srt_path = None

        self.overlay.set_status("Traitement fichier lance... cela peut prendre plusieurs minutes.")
        self._media_cancel_event = threading.Event()
        self._create_media_progress_dialog(parent=dialog_parent, media_path=media_path)

        def _worker() -> None:
            try:
                service = MediaTranslationService(self.config)
                artifacts = service.translate_media_to_artifacts(
                    media_path,
                    on_status=self.on_pipeline_status,
                    on_progress=lambda percent, step: self.bridge.media_progress.emit(percent, step),
                    cancel_event=self._media_cancel_event,
                )
                if not artifacts.text_output.strip():
                    raise RuntimeError("Aucun texte traduisible detecte dans ce fichier.")

                saved_txt = service.save_translation_output(output_path, artifacts.text_output)
                saved_srt = service.save_srt_output(
                    service.default_srt_output_path(output_path),
                    artifacts.srt_output,
                )
                self._last_media_srt_path = saved_srt
                self.bridge.media_done.emit(f"{saved_txt}||{saved_srt}")
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.exception("Erreur traitement fichier media: %s", exc)
                self.bridge.media_error.emit(f"Echec traduction fichier: {exc}")

        self._media_worker = threading.Thread(target=_worker, name="aura-media-translate", daemon=True)
        self._media_worker.start()

    def _create_media_progress_dialog(self, parent: QWidget, media_path: str) -> None:
        dialog = QProgressDialog("Preparation...", "Annuler", 0, 100, parent)
        dialog.setWindowTitle("Traitement video/audio")
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setMinimumDuration(0)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.setValue(0)
        dialog.setLabelText(f"Analyse du fichier: {Path(media_path).name}\nInitialisation...")
        dialog.canceled.connect(self._cancel_media_translation)
        dialog.show()
        self._media_progress_dialog = dialog

    def _cancel_media_translation(self) -> None:
        if self._media_cancel_event is not None:
            self._media_cancel_event.set()
        self.overlay.set_status("Annulation du traitement fichier en cours...")

    def _on_media_progress(self, percent: int, step: str) -> None:
        normalized = max(0, min(100, int(percent)))
        self.overlay.set_status(step)
        if self._media_progress_dialog is not None:
            self._media_progress_dialog.setValue(normalized)
            self._media_progress_dialog.setLabelText(step)

    def _on_media_done(self, saved_paths: str) -> None:
        txt_path, srt_path = self._split_saved_paths(saved_paths)
        if self._media_progress_dialog is not None:
            self._media_progress_dialog.setValue(100)
            self._media_progress_dialog.setLabelText("Traitement termine.")
            self._media_progress_dialog.close()
            self._media_progress_dialog = None

        self._media_cancel_event = None
        self.bridge.translated.emit("Traduction fichier terminee.")
        self.bridge.status.emit(f"Fichier traduit et exporte: {txt_path} (SRT: {srt_path})")
        QMessageBox.information(
            self.overlay,
            "Traduction fichier",
            "Traduction terminee.\n"
            f"TXT exporte:\n{txt_path}\n\n"
            f"SRT exporte:\n{srt_path}",
        )

    def _on_media_error(self, message: str) -> None:
        if self._media_progress_dialog is not None:
            self._media_progress_dialog.close()
            self._media_progress_dialog = None

        cancelled = self._media_cancel_event.is_set() if self._media_cancel_event is not None else False
        self._media_cancel_event = None
        if cancelled:
            self.bridge.status.emit("Traitement fichier annule.")
            QMessageBox.information(self.overlay, "Traduction fichier", "Traitement annule par l'utilisateur.")
            return

        self.bridge.status.emit(message)
        QMessageBox.critical(self.overlay, "Traduction fichier", message)
        logger.error("Erreur media: %s", message)

    @staticmethod
    def _split_saved_paths(saved_paths: str) -> tuple[str, str]:
        parts = saved_paths.split("||", maxsplit=1)
        if len(parts) == 2:
            return parts[0], parts[1]
        return saved_paths, ""

    def open_vlc_player(self) -> None:
        logger.info("Ouverture lecteur VLC demandee")
        launch_dialog = VlcLaunchDialog(
            base_dir=self.config_path.parent,
            default_media=self._last_media_input_path,
            default_subtitle=self._last_media_srt_path,
            parent=self.overlay,
        )
        if not launch_dialog.exec():
            return

        media_path, subtitle_path = launch_dialog.selected_paths
        self._last_media_input_path = Path(media_path)
        self._last_media_srt_path = Path(subtitle_path) if subtitle_path else self._last_media_srt_path

        if self._vlc_window is None:
            self._vlc_window = VlcPlayerWindow()
            if not self.app_icon.isNull():
                self._vlc_window.setWindowIcon(self.app_icon)

        # Ensure native window is realized before passing handle to VLC.
        self._vlc_window.show()
        self._vlc_window.raise_()
        self._vlc_window.activateWindow()

        opened = self._vlc_window.open_media(self._last_media_input_path, subtitle_path)
        if not opened:
            QMessageBox.warning(
                self.overlay,
                "Lecteur VLC",
                "VLC non disponible. Installe VLC Desktop + python-vlc, puis relance l'application.",
            )

    def quit(self) -> None:
        self._global_hotkeys.stop()
        self.pipeline.stop()
        if self.tray_icon is not None:
            self.tray_icon.hide()
        self.app.quit()

    def toggle_overlay_visibility(self) -> None:
        if self.overlay.isVisible():
            self.hide_overlay()
        else:
            self.show_overlay()

    def show_overlay(self) -> None:
        self.overlay.show()
        self.overlay.showNormal()
        self.overlay.raise_()
        self.overlay.activateWindow()
        self.overlay.repaint()
        if self.tray_icon is not None:
            self.tray_icon.setToolTip("Aura-Traduction (overlay visible)")

    def hide_overlay(self) -> None:
        self.overlay.hide()
        if self.tray_icon is not None:
            self.tray_icon.setToolTip("Aura-Traduction (overlay masque)")

    def _build_system_tray_icon(self) -> QSystemTrayIcon | None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.warning("System tray non disponible. L'application continuera sans icone de notification.")
            return None

        icon = self.app_icon
        if icon.isNull():
            icon = self.app.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        tray = QSystemTrayIcon(icon, self.app)
        tray.setToolTip("Aura-Traduction")

        menu = QMenu()
        show_action = QAction("Ouvrir", self.app)
        hide_action = QAction("Masquer", self.app)
        toggle_action = QAction("Afficher / Masquer overlay", self.app)
        settings_action = QAction("Parametres", self.app)
        restart_action = QAction("Redemarrer la capture", self.app)
        pause_live_action = QAction("Pause traduction live", self.app)
        media_action = QAction("Traduire un fichier video/audio", self.app)
        vlc_action = QAction("Ouvrir lecteur VLC", self.app)
        quit_action = QAction("Quitter", self.app)

        show_action.triggered.connect(self.show_overlay)
        hide_action.triggered.connect(self.hide_overlay)
        toggle_action.triggered.connect(self.toggle_overlay_visibility)
        settings_action.triggered.connect(self.open_settings)
        restart_action.triggered.connect(self.restart_pipeline)
        pause_live_action.triggered.connect(self.toggle_live_translation)
        media_action.triggered.connect(self.process_media_file)
        vlc_action.triggered.connect(self.open_vlc_player)
        quit_action.triggered.connect(self.quit)

        menu.addAction(show_action)
        menu.addAction(hide_action)
        menu.addAction(toggle_action)
        menu.addAction(settings_action)
        menu.addAction(restart_action)
        menu.addAction(pause_live_action)
        menu.addAction(media_action)
        menu.addAction(vlc_action)
        menu.addSeparator()
        menu.addAction(quit_action)

        self._pause_live_action = pause_live_action
        self._update_pause_action_label()

        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        return tray

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_overlay()

    def _update_pause_action_label(self) -> None:
        if self._pause_live_action is not None:
            self._pause_live_action.setText("Reprendre traduction live" if self._live_paused else "Pause traduction live")

    @staticmethod
    def _resolve_config_path() -> Path:
        if getattr(sys, "frozen", False):
            base_dir = Path(sys.executable).resolve().parent
        else:
            base_dir = Path(__file__).resolve().parent

        config_dir = base_dir / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "app_config.json"

    @staticmethod
    def _configure_runtime_logging(base_dir: Path) -> None:
        logs_dir = base_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = logs_dir / "aura-translat-debug.log"

        root_logger = logging.getLogger()

        file_handler_exists = False
        for handler in root_logger.handlers:
            if isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == log_file:
                file_handler_exists = True
                break

        if not file_handler_exists:
            try:
                file_handler = logging.FileHandler(log_file, encoding="utf-8")
                file_handler.setLevel(logging.DEBUG)
                file_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
                root_logger.addHandler(file_handler)
            except (OSError, IOError) as e:
                print(f"Impossible d'initialiser log fichier {log_file}: {e}")
                logger.warning("Logging fichier desactive: %s", e)

        root_logger.setLevel(logging.DEBUG)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("filelock").setLevel(logging.WARNING)
        logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
        logging.getLogger("faster_whisper").setLevel(logging.INFO)
        logger.debug("Logging fichier actif: %s", log_file)

    @staticmethod
    def _resolve_app_icon() -> QIcon:
        candidates: list[Path] = []

        if getattr(sys, "frozen", False):
            if hasattr(sys, "_MEIPASS"):
                candidates.append(Path(sys._MEIPASS) / "icon" / "Aura-Translat.png")
            candidates.append(Path(sys.executable).resolve().parent / "icon" / "Aura-Translat.png")
        else:
            candidates.append(Path(__file__).resolve().parent / "icon" / "Aura-Translat.png")

        for candidate in candidates:
            if candidate.exists():
                return QIcon(str(candidate))

        return QIcon()


if __name__ == "__main__":
    app = AuraTraductionApp()
    raise SystemExit(app.run())

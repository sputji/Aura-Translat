from __future__ import annotations

from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from config.settings import ShortcutsConfig


class ShortcutSettingsDialog(QDialog):
    def __init__(self, shortcuts: ShortcutsConfig, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Raccourcis clavier")
        self.setMinimumWidth(520)

        self._pause_edit = QKeySequenceEdit(QKeySequence(shortcuts.toggle_pause), self)
        self._settings_edit = QKeySequenceEdit(QKeySequence(shortcuts.open_settings), self)
        self._overlay_edit = QKeySequenceEdit(QKeySequence(shortcuts.toggle_overlay), self)

        form = QFormLayout()
        form.addRow("Pause/Reprise live", self._pause_edit)
        form.addRow("Ouvrir parametres", self._settings_edit)
        form.addRow("Masquer/Afficher overlay", self._overlay_edit)

        save_btn = QPushButton("Enregistrer")
        cancel_btn = QPushButton("Annuler")
        reset_btn = QPushButton("Par defaut")

        save_btn.clicked.connect(self._save)
        cancel_btn.clicked.connect(self.reject)
        reset_btn.clicked.connect(self._reset_defaults)

        footer = QHBoxLayout()
        footer.addWidget(reset_btn)
        footer.addStretch(1)
        footer.addWidget(cancel_btn)
        footer.addWidget(save_btn)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addLayout(footer)

        self._result = shortcuts

    @property
    def result(self) -> ShortcutsConfig:
        return self._result

    def _reset_defaults(self) -> None:
        self._pause_edit.setKeySequence(QKeySequence("Ctrl+Shift+P"))
        self._settings_edit.setKeySequence(QKeySequence("Ctrl+Shift+S"))
        self._overlay_edit.setKeySequence(QKeySequence("Ctrl+Shift+H"))

    def _save(self) -> None:
        pause = self._pause_edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText).strip()
        settings = self._settings_edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText).strip()
        overlay = self._overlay_edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText).strip()

        if not pause or not settings or not overlay:
            QMessageBox.warning(self, "Raccourcis", "Tous les raccourcis doivent etre renseignes.")
            return

        if len({pause, settings, overlay}) < 3:
            QMessageBox.warning(self, "Raccourcis", "Chaque action doit avoir un raccourci different.")
            return

        self._result = ShortcutsConfig(
            toggle_pause=pause,
            open_settings=settings,
            toggle_overlay=overlay,
        )
        self.accept()

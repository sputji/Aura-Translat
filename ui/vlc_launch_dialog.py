from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)


class VlcLaunchDialog(QDialog):
    def __init__(
        self,
        base_dir: Path,
        default_media: Path | None = None,
        default_subtitle: Path | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Lecteur VLC - Ouvrir media")
        self.setMinimumWidth(700)

        self._base_dir = base_dir

        self._media_edit = QLineEdit()
        self._subtitle_edit = QLineEdit()

        browse_media_btn = QPushButton("Parcourir...")
        browse_subtitle_btn = QPushButton("Sous-titres...")
        launch_btn = QPushButton("Ouvrir dans VLC")
        cancel_btn = QPushButton("Annuler")

        browse_media_btn.clicked.connect(self._browse_media)
        browse_subtitle_btn.clicked.connect(self._browse_subtitle)
        launch_btn.clicked.connect(self._on_launch)
        cancel_btn.clicked.connect(self.reject)

        form = QFormLayout()

        media_row = QHBoxLayout()
        media_row.addWidget(self._media_edit, stretch=1)
        media_row.addWidget(browse_media_btn)
        form.addRow("Fichier media", media_row)

        subtitle_row = QHBoxLayout()
        subtitle_row.addWidget(self._subtitle_edit, stretch=1)
        subtitle_row.addWidget(browse_subtitle_btn)
        form.addRow("Fichier SRT (optionnel)", subtitle_row)

        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(cancel_btn)
        footer.addWidget(launch_btn)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addLayout(footer)

        if default_media is not None and default_media.exists():
            self._media_edit.setText(str(default_media))
            if default_subtitle is None:
                candidate = default_media.with_suffix(".srt")
                if candidate.exists():
                    default_subtitle = candidate

        if default_subtitle is not None and default_subtitle.exists():
            self._subtitle_edit.setText(str(default_subtitle))

    @property
    def selected_paths(self) -> tuple[str, str]:
        return self._media_edit.text().strip(), self._subtitle_edit.text().strip()

    def _browse_media(self) -> None:
        dialog = QFileDialog(
            self,
            "Choisir un fichier media pour lecture VLC",
            str(self._base_dir),
            "Media (*.mp4 *.mkv *.mov *.avi *.webm *.mp3 *.wav *.m4a *.flac *.aac);;Tous les fichiers (*.*)",
        )
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        if not dialog.exec():
            return

        selected = dialog.selectedFiles()
        if not selected:
            return

        selected_media = Path(selected[0])
        self._media_edit.setText(str(selected_media))
        if not self._subtitle_edit.text().strip():
            candidate = selected_media.with_suffix(".srt")
            if candidate.exists():
                self._subtitle_edit.setText(str(candidate))

    def _browse_subtitle(self) -> None:
        dialog = QFileDialog(
            self,
            "Choisir un fichier de sous-titres",
            str(self._base_dir),
            "Sous-titres (*.srt);;Tous les fichiers (*.*)",
        )
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        if not dialog.exec():
            return

        selected = dialog.selectedFiles()
        if selected:
            self._subtitle_edit.setText(selected[0])

    def _on_launch(self) -> None:
        media_path = self._media_edit.text().strip()
        subtitle_path = self._subtitle_edit.text().strip()

        if not media_path:
            QMessageBox.warning(self, "Lecteur VLC", "Choisis un fichier media.")
            return
        if not Path(media_path).exists():
            QMessageBox.warning(self, "Lecteur VLC", "Le fichier media selectionne est introuvable.")
            return
        if subtitle_path and not Path(subtitle_path).exists():
            QMessageBox.warning(self, "Lecteur VLC", "Le fichier SRT selectionne est introuvable.")
            return

        self.accept()

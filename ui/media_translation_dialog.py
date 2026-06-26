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

from core.media_translation import MediaTranslationService


class MediaTranslationDialog(QDialog):
    def __init__(self, base_dir: Path, default_input: Path | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Traduction fichier video/audio")
        self.setMinimumWidth(700)

        self._base_dir = base_dir

        self._input_edit = QLineEdit()
        self._output_edit = QLineEdit()

        browse_input_btn = QPushButton("Parcourir...")
        browse_output_btn = QPushButton("Sortie...")
        launch_btn = QPushButton("Lancer la traduction")
        cancel_btn = QPushButton("Annuler")

        browse_input_btn.clicked.connect(self._browse_input)
        browse_output_btn.clicked.connect(self._browse_output)
        launch_btn.clicked.connect(self._on_launch)
        cancel_btn.clicked.connect(self.reject)

        form = QFormLayout()

        input_row = QHBoxLayout()
        input_row.addWidget(self._input_edit, stretch=1)
        input_row.addWidget(browse_input_btn)
        form.addRow("Fichier media", input_row)

        output_row = QHBoxLayout()
        output_row.addWidget(self._output_edit, stretch=1)
        output_row.addWidget(browse_output_btn)
        form.addRow("Fichier TXT sortie", output_row)

        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(cancel_btn)
        footer.addWidget(launch_btn)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addLayout(footer)

        if default_input is not None and default_input.exists():
            self._input_edit.setText(str(default_input))
            self._output_edit.setText(str(MediaTranslationService.default_output_path(default_input)))

    @property
    def selected_paths(self) -> tuple[str, str]:
        return self._input_edit.text().strip(), self._output_edit.text().strip()

    def _browse_input(self) -> None:
        dialog = QFileDialog(
            self,
            "Choisir un fichier video/audio a traduire",
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
        self._input_edit.setText(str(selected_media))
        if not self._output_edit.text().strip():
            self._output_edit.setText(str(MediaTranslationService.default_output_path(selected_media)))

    def _browse_output(self) -> None:
        default_output = self._output_edit.text().strip()
        if not default_output:
            source = self._input_edit.text().strip()
            if source:
                default_output = str(MediaTranslationService.default_output_path(source))
            else:
                default_output = str(self._base_dir / "traduction-media.txt")

        dialog = QFileDialog(self, "Enregistrer la traduction complete", default_output, "Texte (*.txt)")
        dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        dialog.setDefaultSuffix("txt")
        if not dialog.exec():
            return

        selected = dialog.selectedFiles()
        if selected:
            self._output_edit.setText(selected[0])

    def _on_launch(self) -> None:
        media_path = self._input_edit.text().strip()
        output_path = self._output_edit.text().strip()

        if not media_path:
            QMessageBox.warning(self, "Traduction fichier", "Choisis un fichier media.")
            return
        if not Path(media_path).exists():
            QMessageBox.warning(self, "Traduction fichier", "Le fichier media selectionne est introuvable.")
            return
        if not output_path:
            QMessageBox.warning(self, "Traduction fichier", "Choisis un fichier de sortie TXT.")
            return

        self.accept()

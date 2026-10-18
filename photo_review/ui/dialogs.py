"""Dialogs: first-launch notice, settings, export options and summary."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import export
from ..config import Config


def first_launch_notice(parent: QWidget, config: Config) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("Before you start")
    box.setText(
        "<b>Please back up your photo folder first.</b><br><br>"
        "This app writes ratings, captions, tags and dates into your photo files, and moves "
        "photos you archive into an <b>_archive</b> folder."
    )
    dont = QCheckBox("Don't show this again")
    box.setCheckBox(dont)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()
    if dont.isChecked():
        config.first_launch_notice_dismissed = True


class SettingsDialog(QDialog):
    """Metadata settings (decision Q13)."""

    def __init__(self, parent: QWidget, config: Config) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.config = config
        self.mirror = QCheckBox("Mirror captions to legacy fields (EXIF, IPTC, Windows)")
        self.mirror.setChecked(config.mirror_legacy)
        self.mirror.setToolTip(
            "Also write the caption to EXIF ImageDescription and, for JPEG, to IPTC Caption-Abstract "
            "and the Windows comment, so more programs can see it."
        )
        self.create = QCheckBox("Also set CreateDate when saving a date")
        self.create.setChecked(config.set_create_date)
        self.create.setToolTip("Off by default: on scans, CreateDate records when the photo was scanned.")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(self.mirror)
        lay.addWidget(self.create)
        lay.addWidget(buttons)

    def accept(self) -> None:
        self.config.mirror_legacy = self.mirror.isChecked()
        self.config.set_create_date = self.create.isChecked()
        super().accept()


class ExportDialog(QDialog):
    """Export options (the two export settings live here, decision Q13)."""

    def __init__(self, parent: QWidget, config: Config, count: int) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export for photo book")
        self.config = config
        intro = QLabel(
            f"Copies the <b>{count}</b> photos in the current view, in the current order, to a new "
            "folder for Mixbook or Shutterfly. Your original photos are not changed. "
            "HEIC and TIFF photos are converted to JPEG. A <b>captions.csv</b> file is included."
        )
        intro.setWordWrap(True)
        self.prefix = QCheckBox("Prefix file names with sequence numbers (0001_, 0002_, …)")
        self.prefix.setChecked(config.export_sequence_prefix)
        self.burn = QCheckBox("Burn caption into image (black bar at the bottom)")
        self.burn.setChecked(False)
        self.percent = QSpinBox()
        self.percent.setRange(export.BAR_PERCENT_MIN, export.BAR_PERCENT_MAX)
        self.percent.setSuffix(" % of photo height")
        self.percent.setValue(config.caption_bar_percent)
        self.percent.setEnabled(False)
        self.burn.toggled.connect(self.percent.setEnabled)
        form = QFormLayout()
        form.addRow(self.prefix)
        form.addRow(self.burn)
        form.addRow("Caption bar height:", self.percent)
        buttons = QDialogButtonBox()
        buttons.addButton("Choose folder and export…", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addLayout(form)
        lay.addWidget(buttons)

    def options(self) -> export.ExportOptions:
        self.config.export_sequence_prefix = self.prefix.isChecked()
        self.config.caption_bar_percent = self.percent.value()
        return export.ExportOptions(self.prefix.isChecked(), self.burn.isChecked(), self.percent.value())


def choose_export_folder(parent: QWidget, start: str) -> Path | None:
    """Pick an output folder. A non-empty folder offers "Choose another folder" or "Cancel" (Q16)."""
    while True:
        path = QFileDialog.getExistingDirectory(parent, "Choose an EMPTY folder for the export", start)
        if not path:
            return None
        folder = Path(path)
        if export.folder_is_empty(folder):
            return folder
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Folder is not empty")
        box.setText(
            f"{folder} already contains files.\n\nExport needs an empty folder so nothing is overwritten."
        )
        again = box.addButton("Choose another folder", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is not again:
            return None
        start = str(folder)


def export_summary(parent: QWidget, out_dir: Path, s: export.ExportSummary) -> None:
    lines = [f"Exported {len(s.exported)} photos to {out_dir}."]
    if s.converted:
        lines.append(f"Converted to JPEG: {len(s.converted)}.")
    if s.burned:
        lines.append(f"Captions burned in: {len(s.burned)}.")
    if s.truncated:
        lines.append("\nCaptions too long for the bar (shortened with …):\n  " + "\n  ".join(s.truncated))
    if s.renamed:
        lines.append("\nRenamed to avoid a duplicate name:\n  " + "\n  ".join(s.renamed))
    if s.not_srgb:
        lines.append(
            "\nColour could not be converted to sRGB (exported as-is):\n  " + "\n  ".join(s.not_srgb)
        )
    if s.skipped:
        lines.append("\nSkipped:\n  " + "\n  ".join(f"{n}: {why}" for n, why in s.skipped))
    dlg = QDialog(parent)
    dlg.setWindowTitle("Export finished")
    text = QPlainTextEdit("\n".join(lines))
    text.setReadOnly(True)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
    buttons.accepted.connect(dlg.accept)
    lay = QVBoxLayout(dlg)
    lay.addWidget(text)
    lay.addWidget(buttons)
    dlg.resize(560, 360)
    dlg.exec()

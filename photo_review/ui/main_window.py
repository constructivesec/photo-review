"""Main window (spec sections 4 and 5)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QEvent, QObject, QSize, QStringListModel, Qt, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QFont, QKeyEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, date_logic, export, file_ops, tag_logic
from .. import metadata as md
from ..config import Config, config_dir
from . import actions, dialogs
from .decoder import Decoder
from .model import SORT_OPTIONS, ReviewModel, needs_metadata
from .widgets import AMBER, PhotoView, TagChips, archive_icon, arrow_icon, info_icon, keep_icon, undo_icon

log = logging.getLogger(__name__)

SCANNER_TERMS = ("epson", "canoscan", "fujitsu", "plustek", "doxie", "flip-pal", "flippal", "scansnap")
BATCH = 200
EMPTY_NO_FOLDER = 'Click "Select Folder" to choose a folder of photos.'
EMPTY_NO_PHOTOS = "There are no photos in this folder.\n\nSupported: JPG, JPEG, PNG, HEIC, HEIF, TIF, TIFF."
EMPTY_NO_ARCHIVED = "No archived photos."
EMPTY_NO_UNDATED = "All photos in this view have a date."
FINISHED = "Finished! You have reached the last photo."


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} bytes"


class MainWindow(QMainWindow):
    def __init__(self, config: Config, config_path: Path | None = None) -> None:
        super().__init__()
        self.config = config
        self.config_path = config_path
        self.model = ReviewModel(sort_key=config.sort_key)
        self.exiftool_version = ""
        self._jobs: dict[int, Callable[[Any, BaseException | None], None]] = {}
        self._busy = 0
        self._ready = False  # ExifTool started
        self._folder_tags: set[str] = set()
        self._job_busy: set[int] = set()

        from .worker import BACKGROUND, USER, Worker

        self._USER, self._BACKGROUND = USER, BACKGROUND
        self.worker = Worker(self)
        self.worker.done.connect(self._on_job_done)
        self.worker.progress.connect(self._on_job_progress)
        self.worker.started_ok.connect(self._on_worker_started)
        self.worker.failed_to_start.connect(self._on_worker_failed)
        self.decoder = Decoder(self)
        self.decoder.ready.connect(self._on_decoded)

        self.setWindowTitle("Photo Review")
        self.resize(1280, 900)
        self.setMinimumSize(900, 680)
        self._build()
        if config.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(config.window_geometry.encode()))
        QApplication.instance().installEventFilter(self)  # type: ignore[union-attr]
        self.view.show_message("Starting…")
        self.refresh_controls()
        self.worker.start()

    # ================================================================ layout

    def _big_button(self, icon: Any, text: str, key: str, tip: str) -> QToolButton:
        b = QToolButton()
        b.setIcon(icon)
        b.setIconSize(QSize(40, 40))
        b.setText(f"{text}\n[{key}]")
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        b.setMinimumSize(96, 88)
        b.setToolTip(f"{tip} ({key})")
        b.setAccessibleName(text)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        f = b.font()
        f.setBold(True)
        f.setPointSize(f.pointSize() + 2)
        b.setFont(f)
        return b

    def _build(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 8, 10, 4)
        big = QFont(self.font())
        big.setPointSize(big.pointSize() + 2)

        # Top bar
        top = QHBoxLayout()
        self.select_btn = QPushButton("Select Folder…")
        self.select_btn.setFont(big)
        self.select_btn.setMinimumHeight(40)
        self.select_btn.clicked.connect(self.choose_folder)
        self.folder_label = QLabel("No folder selected")
        self.folder_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.sort_combo = QComboBox()
        for key, label in SORT_OPTIONS:
            self.sort_combo.addItem(label, key)
        self.sort_combo.setCurrentIndex([k for k, _ in SORT_OPTIONS].index(self.model.sort_key))
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        self.show_archived = QCheckBox("Show archived")
        self.show_archived.toggled.connect(self._on_toggle_archived)
        self.undated_only = QCheckBox("Show undated only")
        self.undated_only.toggled.connect(self._on_toggle_undated)
        self.refresh_btn = QPushButton("Refresh [F5]")
        self.refresh_btn.setToolTip("Rescan the folder (F5)")
        self.refresh_btn.clicked.connect(self.refresh_folder)
        for w in (self.select_btn, self.sort_combo, self.show_archived, self.undated_only, self.refresh_btn):
            w.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        top.addWidget(self.select_btn)
        top.addWidget(self.folder_label, 1)
        top.addWidget(QLabel("Sort:"))
        top.addWidget(self.sort_combo)
        top.addWidget(self.show_archived)
        top.addWidget(self.undated_only)
        top.addWidget(self.refresh_btn)
        root.addLayout(top)

        self.banner = QLabel()
        self.banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.banner.setStyleSheet("background:#1b5e20;color:white;padding:6px;border-radius:4px;")
        self.banner.setFont(big)
        self.banner.hide()
        root.addWidget(self.banner)

        self.view = PhotoView()
        root.addWidget(self.view, 1)

        # Counter and info strip
        self.counter = QLabel("0 of 0")
        self.counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cf = QFont(self.font())
        cf.setPointSize(cf.pointSize() + 8)
        cf.setBold(True)
        self.counter.setFont(cf)
        root.addWidget(self.counter)
        strip = QHBoxLayout()
        self.info_name = QLabel()
        self.info_rating = QLabel()
        self.info_dims = QLabel()
        self.info_size = QLabel()
        self.info_nodate = QLabel("⚠ No date")
        self.info_nodate.setStyleSheet(f"color:{AMBER.name()};font-weight:bold;")
        self.info_nodate.hide()
        for w in (self.info_name, self.info_rating, self.info_dims, self.info_size):
            w.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        strip.addStretch(1)
        for w in (self.info_name, self.info_rating, self.info_dims, self.info_size, self.info_nodate):
            strip.addWidget(w)
            strip.addSpacing(18)
        strip.addStretch(1)
        root.addLayout(strip)

        # Big controls
        self.back_btn = self._big_button(arrow_icon(True), "Back", "←", "Previous photo")
        self.archive_btn = self._big_button(
            archive_icon(), "ARCHIVE", "N", "Archive: move to the _archive folder"
        )
        self.keep_btn = self._big_button(keep_icon(), "KEEP", "Y", "Keep: mark with 1 star")
        self.next_btn = self._big_button(arrow_icon(False), "Next", "→", "Next photo")
        undo_key = QKeySequence(QKeySequence.StandardKey.Undo).toString(
            QKeySequence.SequenceFormat.NativeText
        )
        self.undo_btn = self._big_button(undo_icon(), "Undo", undo_key, "Undo the last action")
        self.info_btn = self._big_button(info_icon(), "Info", "I", "Show or hide photo details")
        self.archive_btn.setStyleSheet("QToolButton{color:#b71c1c;}")
        self.keep_btn.setStyleSheet("QToolButton{color:#1b5e20;}")
        self.back_btn.clicked.connect(self.go_back)
        self.archive_btn.clicked.connect(self.do_archive)
        self.keep_btn.clicked.connect(self.do_keep)
        self.next_btn.clicked.connect(self.go_next)
        self.undo_btn.clicked.connect(self.do_undo)
        self.info_btn.clicked.connect(self.toggle_info)
        controls = QHBoxLayout()
        controls.addStretch(1)
        for b in (self.back_btn, self.archive_btn, self.keep_btn, self.next_btn):
            controls.addWidget(b)
        controls.addSpacing(28)
        controls.addWidget(self.undo_btn)
        controls.addWidget(self.info_btn)
        controls.addStretch(1)
        root.addLayout(controls)

        # Date row
        date_row = QHBoxLayout()
        self.month_edit = QLineEdit()
        self.month_edit.setPlaceholderText("MM")
        self.month_edit.setMaximumWidth(60)
        self.month_edit.setAccessibleName("Month")
        self.year_edit = QLineEdit()
        self.year_edit.setPlaceholderText("YYYY")
        self.year_edit.setMaximumWidth(80)
        self.year_edit.setAccessibleName("Year")
        self.approx_box = QCheckBox("Approximate date")
        self.approx_box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.save_date_btn = QPushButton("Save Date")
        self.save_date_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.save_date_btn.clicked.connect(self.do_save_date)
        self.month_edit.returnPressed.connect(self.do_save_date)
        self.year_edit.returnPressed.connect(self.do_save_date)
        self.stored_date = QLabel("Stored date: –")
        self.stored_date.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.date_mismatch = QLabel("⚠ EXIF and XMP dates differ (showing EXIF)")
        self.date_mismatch.setStyleSheet(f"color:{AMBER.name()};font-weight:bold;")
        self.date_mismatch.hide()
        date_row.addWidget(QLabel("Month:"))
        date_row.addWidget(self.month_edit)
        date_row.addWidget(QLabel("Year:"))
        date_row.addWidget(self.year_edit)
        date_row.addWidget(self.approx_box)
        date_row.addWidget(self.save_date_btn)
        date_row.addSpacing(16)
        date_row.addWidget(self.stored_date)
        date_row.addWidget(self.date_mismatch)
        date_row.addStretch(1)
        root.addLayout(date_row)

        # Caption row
        cap_row = QHBoxLayout()
        self.caption_edit = QLineEdit()
        self.caption_edit.setFont(big)
        self.caption_edit.setAccessibleName("Caption")
        self.save_caption_btn = QPushButton("Save Caption")
        self.save_caption_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.save_caption_btn.clicked.connect(self.do_save_caption)
        cap_row.addWidget(QLabel("Caption:"))
        cap_row.addWidget(self.caption_edit, 1)
        cap_row.addWidget(self.save_caption_btn)
        root.addLayout(cap_row)

        # Tag row
        tag_row = QHBoxLayout()
        self.tag_edit = QLineEdit()
        self.tag_edit.setPlaceholderText("Add a tag, for example person:emma, then press Enter")
        self.tag_edit.setAccessibleName("New tag")
        self.tag_edit.setMinimumWidth(260)
        self._completer_model = QStringListModel(self)
        completer = QCompleter(self._completer_model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.tag_edit.setCompleter(completer)
        self.tag_edit.returnPressed.connect(self.do_add_tag)
        self.add_tag_btn = QPushButton("Add Tag")
        self.add_tag_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.add_tag_btn.clicked.connect(self.do_add_tag)
        tag_row.addWidget(QLabel("Tags:"))
        tag_row.addWidget(self.tag_edit)
        self.quick_btns = []
        for prefix in tag_logic.QUICK_PREFIXES:
            b = QPushButton(f"{prefix}:")
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setToolTip(f"Start a {prefix} tag")
            b.clicked.connect(lambda _=False, pf=prefix: self._quick_prefix(pf))
            tag_row.addWidget(b)
            self.quick_btns.append(b)
        tag_row.addWidget(self.add_tag_btn)
        self.chips = TagChips()
        self.chips.remove_requested.connect(self.do_remove_tag)
        tag_row.addWidget(self.chips, 1)
        root.addLayout(tag_row)

        self.setCentralWidget(central)

        # Info side panel
        self.info_dock = QDockWidget("Info", self)
        self.info_dock.setObjectName("info")
        self.info_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)
        panel = QWidget()
        form = QFormLayout(panel)
        self.i_device = QLabel()
        self.i_scan = QLabel("Scan?")
        self.i_scan.setToolTip("The device name looks like a scanner, so the date may be the scan date.")
        self.i_scan.setStyleSheet(f"color:{AMBER.name()};font-weight:bold;")
        self.i_gps = QLabel()
        self.i_gps.setOpenExternalLinks(True)
        self.i_gps.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.show_gps = QCheckBox("Show GPS")
        self.show_gps.setChecked(self.config.show_gps)
        self.show_gps.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.show_gps.toggled.connect(self._on_show_gps)
        self.i_shutter, self.i_aperture, self.i_iso, self.i_focal = QLabel(), QLabel(), QLabel(), QLabel()
        dev = QHBoxLayout()
        dev.addWidget(self.i_device)
        dev.addWidget(self.i_scan)
        dev.addStretch(1)
        form.addRow("Device:", dev)
        form.addRow("GPS:", self.i_gps)
        form.addRow("", self.show_gps)
        form.addRow("Shutter speed:", self.i_shutter)
        form.addRow("Aperture:", self.i_aperture)
        form.addRow("ISO:", self.i_iso)
        form.addRow("Focal length:", self.i_focal)
        for lbl in (self.i_device, self.i_shutter, self.i_aperture, self.i_iso, self.i_focal):
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.info_dock.setWidget(panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.info_dock)
        self.info_dock.hide()

        # Menu
        file_menu = self.menuBar().addMenu("&File")
        self.select_action = QAction("Select Folder…", self, triggered=self.choose_folder)
        self.export_action = QAction("Export for photo book…", self, triggered=self.do_export)
        settings_action = QAction("Settings…", self, triggered=self.open_settings)
        logs_action = QAction("Open log folder", self, triggered=self.open_log_folder)
        quit_action = QAction("Quit", self, triggered=self.close)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        for a in (self.select_action, self.export_action, settings_action, logs_action):
            file_menu.addAction(a)
        file_menu.addSeparator()
        file_menu.addAction(quit_action)
        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(QAction("About Photo Review", self, triggered=self.show_about))

        # Status bar
        self.status = QLabel()
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status.setWordWrap(True)
        self.statusBar().addWidget(self.status, 1)
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(220)
        self.progress.hide()
        self.statusBar().addPermanentWidget(self.progress)

    # ================================================================ status and state

    def show_status(self, text: str, error: bool = False) -> None:
        self.status.setStyleSheet("color:#b71c1c;font-weight:bold;" if error else "")
        self.status.setText(text)

    def show_banner(self, text: str | None) -> None:
        self.banner.setVisible(bool(text))
        self.banner.setText(text or "")

    def set_busy(self, busy: bool) -> None:
        self._busy += 1 if busy else -1
        self._busy = max(0, self._busy)
        if busy:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        else:
            QApplication.restoreOverrideCursor()
        self.refresh_controls()

    @property
    def busy(self) -> bool:
        return self._busy > 0 or not self._ready

    def refresh_controls(self) -> None:
        m = self.model
        meta = m.meta(m.current)
        has = m.current is not None and not self.busy
        writable = has and meta is not None
        self.counter.setText(f"{m.position} of {m.total}")
        self.back_btn.setEnabled(has and m.can_back())
        self.next_btn.setEnabled(has and m.can_next())
        self.keep_btn.setEnabled(writable)
        self.archive_btn.setEnabled(writable and not m.archived_view)
        self.undo_btn.setEnabled(bool(m.undo) and not self.busy)
        for w in (
            self.month_edit,
            self.year_edit,
            self.approx_box,
            self.save_date_btn,
            self.caption_edit,
            self.save_caption_btn,
            self.tag_edit,
            self.add_tag_btn,
            *self.quick_btns,
        ):
            w.setEnabled(writable)
        self.chips.setEnabled(writable)
        for w in (self.select_btn, self.sort_combo, self.show_archived, self.undated_only, self.refresh_btn):
            w.setEnabled(not self.busy)
        self.select_action.setEnabled(not self.busy)
        self.export_action.setEnabled(
            not self.busy and m.folder is not None and not m.archived_view and m.total > 0
        )

    # ================================================================ worker plumbing

    def run_job(
        self,
        fn: Callable[..., Any],
        on_done: Callable[[Any, BaseException | None], None],
        background: bool = False,
    ) -> None:
        job = self.worker.submit(fn, self._BACKGROUND if background else self._USER)
        self._jobs[job] = on_done
        if not background:
            self._job_busy.add(job)
            self.set_busy(True)

    def _on_job_done(self, job: int, result: Any, exc: BaseException | None) -> None:
        cb = self._jobs.pop(job, None)
        if job in self._job_busy:
            self._job_busy.discard(job)
            self.set_busy(False)
        if cb is not None:
            try:
                cb(result, exc)
            except Exception:  # noqa: BLE001 - never crash on a UI callback
                log.exception("UI callback failed")
                self.show_status("Something went wrong; see the log for details.", error=True)
        self.refresh_controls()

    def _on_job_progress(self, job: int, done: int, total: int, name: str) -> None:
        if job in self._job_busy:
            self.progress.show()
            self.progress.setRange(0, max(1, total))
            self.progress.setValue(done)
            if done >= total:
                self.progress.hide()

    def _on_worker_started(self, version: str) -> None:
        self.exiftool_version = version
        self._ready = True
        log.info("ExifTool %s started", version)
        if not self.config.first_launch_notice_dismissed:
            dialogs.first_launch_notice(self, self.config)
            self.config.save(self.config_path)
        folder = Path(self.config.last_folder) if self.config.last_folder else None
        if folder and folder.is_dir():
            self.open_folder(folder)
        else:
            self.view.show_message(EMPTY_NO_FOLDER)
            self.refresh_controls()

    def _on_worker_failed(self, error: str) -> None:
        self.view.show_message("ExifTool could not be started.")
        QMessageBox.critical(
            self,
            "Photo Review cannot start",
            f"ExifTool, which reads and writes photo information, could not be started:\n\n{error}",
        )
        self.show_status(f"ExifTool could not be started: {error}", error=True)

    # ================================================================ folder and listing

    def choose_folder(self) -> None:
        if self.busy:
            return
        path = QFileDialog.getExistingDirectory(
            self, "Select photo folder", self.config.last_folder or str(Path.home())
        )
        if path:
            self.open_folder(Path(path))

    def open_folder(self, folder: Path) -> None:
        if file_ops.is_archive_folder(folder):
            QMessageBox.information(
                self,
                "Choose the main folder",
                "This is an _archive folder. Please select its parent folder instead; "
                'use "Show archived" to see archived photos.',
            )
            return
        self.config.last_folder = str(folder)
        self.config.save(self.config_path)
        self.folder_label.setText(str(folder))
        self.folder_label.setToolTip(str(folder))
        self.model = ReviewModel(folder=folder, sort_key=self.model.sort_key)
        self.show_archived.blockSignals(True)
        self.undated_only.blockSignals(True)
        self.show_archived.setChecked(False)
        self.undated_only.setChecked(False)
        self.show_archived.blockSignals(False)
        self.undated_only.blockSignals(False)
        self.decoder.clear()
        self._folder_tags.clear()
        self.load_listing(keep=None, report_missing=False)

    def load_listing(self, keep: Path | None, report_missing: bool) -> None:
        """Scan the view folder, read metadata if the sort/filter needs it, then show."""
        view_folder = self.model.view_folder
        if view_folder is None:
            return
        before = set(self.model.paths)
        self.show_banner(None)
        self.view.show_message("Loading…")

        def scanned(stamps: Any, exc: BaseException | None) -> None:
            if exc is not None:
                self.show_status(str(exc), error=True)
                stamps = {}
            self.model.set_listing(stamps, keep)
            if report_missing:
                gone = sorted(p.name for p in before - set(stamps))
                if gone:
                    self.show_status(
                        f"Skipped {len(gone)} photo(s) moved or deleted outside the app: "
                        f"{', '.join(gone[:5])}"
                    )
            if needs_metadata(self.model.sort_key, self.model.undated_only):
                self.read_all_metadata(lambda: self._after_listing(keep), foreground=True)
            else:
                self._after_listing(keep)
                self.read_all_metadata(lambda: None, foreground=False)

        self.run_job(lambda et, rp: actions.scan(view_folder), scanned)

    def read_all_metadata(self, then: Callable[[], None], foreground: bool) -> None:
        """Batched ExifTool JSON reads with progress (cached by path, size and mtime)."""
        missing = self.model.missing_metadata()
        if not missing:
            then()
            return

        def job(et: md.ExifToolSession, report: Callable[[int, int, str], None]) -> dict:
            out: dict = {}
            for i in range(0, len(missing), BATCH):
                out.update(actions.read_batch(et, missing[i : i + BATCH]))
                report(min(i + BATCH, len(missing)), len(missing), "")
            return out

        def done(metas: Any, exc: BaseException | None) -> None:
            if foreground:
                self.show_status("")
            if exc is None:
                self.model.store_metas({p: m for p, m in metas.items() if p in self.model.stamps})
                self._collect_tags(metas.values())
            else:
                self.show_status(f"Could not read photo information: {exc}", error=True)
            then()

        if foreground:
            self.show_status(f"Reading photo information for {len(missing)} photos…")
        self.run_job(job, done, background=not foreground)

    def _after_listing(self, keep: Path | None) -> None:
        self.model.rebuild(keep)
        if keep is None:
            self.model.index = 0
        self.show_current()

    def refresh_folder(self) -> None:
        if self.busy or self.model.folder is None:
            return
        self.load_listing(keep=self.model.current, report_missing=True)

    def _on_sort_changed(self, _i: int) -> None:
        key = self.sort_combo.currentData()
        self.config.sort_key = key
        self.config.save(self.config_path)
        self.model.sort_key = key
        if self.model.folder is None:
            return
        cur = self.model.current
        self.read_all_metadata(
            lambda: self._after_listing(cur), foreground=needs_metadata(key, self.model.undated_only)
        )

    def _on_toggle_archived(self, on: bool) -> None:
        if self.model.folder is None:
            return
        self.model.archived_view = on
        self.model.clear_undo()
        self.model.paths, self.model.order, self.model.index = [], [], 0
        self.decoder.clear()
        self.load_listing(keep=None, report_missing=False)

    def _on_toggle_undated(self, on: bool) -> None:
        self.model.undated_only = on
        if self.model.folder is None:
            return
        cur = self.model.current
        self.read_all_metadata(lambda: self._after_listing(cur), foreground=True)

    def _collect_tags(self, metas: Any) -> None:
        for m in metas:
            self._folder_tags.update(k for k in m.keywords if tag_logic.is_user_tag(k))
        self._completer_model.setStringList(
            tag_logic.merge_keywords(sorted(self._folder_tags), self.config.tag_history)
        )

    # ================================================================ display

    def _key(self, path: Path | None) -> tuple | None:
        if path is None or path not in self.model.stamps:
            return None
        st = self.model.stamps[path]
        return (path, st.size, st.mtime_ns)

    def show_current(self) -> None:
        m = self.model
        path = m.current
        self._clear_fields()
        if path is None:
            self.setWindowTitle("Photo Review")
            if m.folder is None:
                message = EMPTY_NO_FOLDER
            elif m.undo:  # the last photo in the view was handled
                message = FINISHED
            elif m.archived_view and not m.paths:
                message = EMPTY_NO_ARCHIVED
            elif m.undated_only and m.paths:
                message = EMPTY_NO_UNDATED
            else:
                message = EMPTY_NO_PHOTOS
            self.view.show_message(message)
            self.refresh_controls()
            return
        self.setWindowTitle(f"Photo Review - {path.name}")
        meta = m.meta(path)
        key = self._key(path)
        cached = self.decoder.get(key) if key else None
        if cached is not None:
            self._show_decoded(path, *cached)
        else:
            self.view.show_message(f"Loading {path.name}…", meta.rating if meta else None)
            self.decoder.request(key)
        self.decoder.request(self._key(m.peek(1)))
        if meta is None:
            self.run_job(
                lambda et, rp, p=path: actions.reread(et, p),
                lambda res, exc, p=path: self._meta_loaded(p, res, exc),
            )
        else:
            self._fill_fields(meta)
        self.refresh_controls()

    def _meta_loaded(self, path: Path, meta: md.PhotoMeta | None, exc: BaseException | None) -> None:
        if exc is not None or meta is None:
            self.show_status(f"Could not read {path.name}: {exc}", error=True)
            return
        if meta.error == "File not found":
            self.show_status(f"{path.name} was moved or deleted outside the app; skipped.")
            if path in self.model.order:
                self.model._remove_current(path)
            self.show_current()
            return
        self.model.store_metas({path: meta})
        self._collect_tags([meta])
        if path == self.model.current:
            if meta.error:
                self.show_status(
                    f"The photo information of {path.name} could not be read; it is treated as empty."
                )
            self._fill_fields(meta)
            cached = self.decoder.get(self._key(path)) if self._key(path) else None
            if cached is not None:
                self._show_decoded(path, *cached)
            else:
                self.decoder.request(self._key(path))
        self.refresh_controls()

    def _on_decoded(self, key: tuple, img: Any, full: Any, error: str) -> None:
        if key == self._key(self.model.current):
            self._show_decoded(key[0], img, full, error)

    def _show_decoded(self, path: Path, img: Any, full: Any, error: str) -> None:
        meta = self.model.meta(path)
        rating = meta.rating if meta else None
        if img is None:
            self.view.show_message(f"This photo cannot be displayed:\n{path.name}", rating)
            self.show_status(f"Cannot display {path.name}: {error}", error=True)
            self.info_dims.setText("")
        else:
            self.view.show_image(img, rating)
            w, h = full
            self.info_dims.setText(f"{w} × {h} ({w * h / 1_000_000:.1f} MP)")

    def _clear_fields(self) -> None:
        for e in (self.month_edit, self.year_edit, self.caption_edit, self.tag_edit):
            e.clear()
        self.caption_edit.setPlaceholderText("")
        self.stored_date.setText("Stored date: –")
        self.date_mismatch.hide()
        self.info_nodate.hide()
        self.chips.set_keywords([], False)
        for lbl in (
            self.info_name,
            self.info_rating,
            self.info_dims,
            self.info_size,
            self.i_device,
            self.i_gps,
            self.i_shutter,
            self.i_aperture,
            self.i_iso,
            self.i_focal,
        ):
            lbl.setText("")
        self.i_scan.hide()

    def _fill_fields(self, meta: md.PhotoMeta) -> None:
        path = meta.path
        self.info_name.setText(path.name)
        r = meta.rating
        self.info_rating.setText(
            "Archived" if r is not None and r < 0 else "Not rated" if not r else f"Kept ({'★' * r})"
        )
        if meta.stamp:
            self.info_size.setText(human_size(meta.stamp.size))
        eff = meta.date
        if eff.value is not None:
            self.month_edit.setText(str(eff.value.month))
            self.year_edit.setText(str(eff.value.year))
            self.stored_date.setText(f"Stored date: {date_logic.format_display(eff.value)}")
        else:
            self.stored_date.setText("Stored date: none")
        self.date_mismatch.setVisible(eff.mismatch)
        self.info_nodate.setVisible(eff.undated)
        self.approx_box.setChecked(meta.approximate or eff.undated)
        self.caption_edit.setText(meta.caption)
        self.caption_edit.setCursorPosition(0)
        self.caption_edit.setPlaceholderText(meta.caption_placeholder or "Type a caption")
        self.chips.set_keywords(meta.keywords, not self.busy)
        make, model = meta.get("IFD0:Make") or "", meta.get("IFD0:Model") or ""
        device = " ".join(x for x in (make, model) if x) or "Unknown"
        self.i_device.setText(device)
        self.i_scan.setVisible(any(t in device.lower() for t in SCANNER_TERMS))
        self._show_gps_text(meta)
        self.i_shutter.setText(meta.get("ExifIFD:ExposureTime") or "–")
        fn = meta.get("ExifIFD:FNumber")
        self.i_aperture.setText(f"f/{fn}" if fn else "–")
        self.i_iso.setText(meta.get("ExifIFD:ISO") or "–")
        self.i_focal.setText(meta.get("ExifIFD:FocalLength") or "–")
        self.view._rating = r  # badge follows the metadata even before the image arrives
        self.view.update()

    def _show_gps_text(self, meta: md.PhotoMeta | None) -> None:
        gps = meta.gps if meta else None
        if gps is None:
            self.i_gps.setText("None")
        elif not self.show_gps.isChecked():
            self.i_gps.setText("Hidden (tick Show GPS)")
        else:
            lat, lon = gps
            url = f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map=16/{lat:.6f}/{lon:.6f}"
            self.i_gps.setText(f'{lat:.6f}, {lon:.6f} &nbsp; <a href="{url}">Open in Maps</a>')

    def _on_show_gps(self, on: bool) -> None:
        self.config.show_gps = on
        self.config.save(self.config_path)
        self._show_gps_text(self.model.meta(self.model.current))

    def toggle_info(self) -> None:
        self.info_dock.setVisible(not self.info_dock.isVisible())

    # ================================================================ navigation

    def go_next(self) -> None:
        if not self.busy and self.model.can_next():
            self.model.next()
            self.show_banner(None)
            self.show_current()

    def go_back(self) -> None:
        if not self.busy and self.model.can_back():
            self.model.back()
            self.show_banner(None)
            self.show_current()

    # ================================================================ actions

    def _current_meta(self) -> md.PhotoMeta | None:
        if self.busy or self.model.current is None:
            return None
        return self.model.meta(self.model.current)

    def _failed(self, meta: md.PhotoMeta, exc: BaseException) -> None:
        self.show_status(str(exc), error=True)
        if isinstance(exc, file_ops.StaleFileError):  # reload and cancel (decision Q20)
            path = meta.path
            self.run_job(
                lambda et, rp: actions.reread(et, path), lambda res, e: self._meta_loaded(path, res, e)
            )

    def do_keep(self) -> None:
        meta = self._current_meta()
        if meta is None:
            return
        m = self.model
        assert m.folder is not None

        def done(res: actions.ActionResult | None, exc: BaseException | None) -> None:
            if exc is not None or res is None:
                self._failed(meta, exc or RuntimeError("KEEP failed"))
                return
            finished = m.applied_keep(res.old_path, res.path, res.snapshot, res.meta)
            moved = res.path != res.old_path
            self.show_status(
                f"Kept {res.old_path.name}" + (" (moved out of the archive)" if moved else "")
                if res.snapshot is not None
                else f"{res.old_path.name} was already kept"
            )
            self.show_banner(FINISHED if finished and m.current is not None else None)
            self.show_current()

        archived, folder = m.archived_view, m.folder
        self.run_job(lambda et, rp: actions.keep(et, meta, archived, folder), done)

    def do_archive(self) -> None:
        meta = self._current_meta()
        if meta is None or self.model.archived_view:
            return
        folder = self.model.folder
        assert folder is not None

        def done(res: actions.ActionResult | None, exc: BaseException | None) -> None:
            if exc is not None or res is None:
                self._failed(meta, exc or RuntimeError("ARCHIVE failed"))
                return
            finished = self.model.applied_archive(res.old_path, res.path, res.snapshot or {})
            self.show_status(f"Archived {res.old_path.name}")
            self.show_banner(FINISHED if finished and self.model.current is not None else None)
            self.show_current()

        self.run_job(lambda et, rp: actions.archive(et, meta, folder), done)

    def _edit_done(
        self, kind: str, meta: md.PhotoMeta, message: str
    ) -> Callable[[Any, BaseException | None], None]:
        def done(res: actions.ActionResult | None, exc: BaseException | None) -> None:
            if exc is not None or res is None:
                self._failed(meta, exc or RuntimeError(f"{kind} failed"))
                return
            left = self.model.applied_edit(kind, res.path, res.snapshot or {}, res.meta)
            self.show_status(" ".join([message, *res.notes]))
            if left:
                self.show_current()
            elif res.path == self.model.current:
                self._fill_fields(res.meta)
            self.view.setFocus()

        return done

    def do_save_caption(self) -> None:
        meta = self._current_meta()
        if meta is None:
            return
        text, mirror = self.caption_edit.text(), self.config.mirror_legacy
        message = "Caption saved." if text else "Caption cleared."
        self.run_job(
            lambda et, rp: actions.save_caption(et, meta, text, mirror),
            self._edit_done("caption", meta, message),
        )

    def do_save_date(self) -> None:
        meta = self._current_meta()
        if meta is None:
            return
        month, year, approx = self.month_edit.text(), self.year_edit.text(), self.approx_box.isChecked()
        try:
            date_logic.validate(month, year)
        except date_logic.DateInputError as exc:
            self.show_status(str(exc), error=True)  # nothing is written
            return
        create = self.config.set_create_date
        self.run_job(
            lambda et, rp: actions.save_date(et, meta, month, year, approx, create),
            self._edit_done("date", meta, "Date saved."),
        )

    def _quick_prefix(self, prefix: str) -> None:
        self.tag_edit.setText(f"{prefix}:")
        self.tag_edit.setFocus()
        self.tag_edit.setCursorPosition(len(self.tag_edit.text()))

    def do_add_tag(self) -> None:
        meta = self._current_meta()
        if meta is None:
            return
        text = self.tag_edit.text()
        try:
            tag = tag_logic.normalize(text)
        except tag_logic.TagError as exc:
            self.show_status(str(exc), error=True)
            return
        if any(k.casefold() == tag.casefold() for k in meta.keywords):
            self.show_status(f'"{tag}" is already on this photo.')
            self.tag_edit.clear()
            self.view.setFocus()
            return
        self.config.tag_history = tag_logic.update_history(self.config.tag_history, [tag])
        self.config.save(self.config_path)
        self._folder_tags.add(tag)
        self.tag_edit.clear()
        self.run_job(
            lambda et, rp: actions.add_tag(et, meta, tag),
            self._edit_done("tags", meta, f'Tag "{tag}" added.'),
        )

    def do_remove_tag(self, tag: str) -> None:
        meta = self._current_meta()
        if meta is None:
            return
        self.run_job(
            lambda et, rp: actions.remove_tag(et, meta, tag),
            self._edit_done("tags", meta, f'Tag "{tag}" removed.'),
        )

    def do_undo(self) -> None:
        if self.busy or not self.model.undo:
            return
        entry = self.model.undo[-1]

        def done(res: actions.ActionResult | None, exc: BaseException | None) -> None:
            if exc is not None or res is None:
                self.show_status(f"Could not undo: {exc}", error=True)
                return
            self.model.applied_undo(entry, res.path, res.meta)
            self.show_status(f"Undone: {entry.label} on {res.path.name}")
            self.show_banner(None)
            self.show_current()

        self.run_job(lambda et, rp: actions.undo(et, entry), done)

    # ================================================================ export, settings, about

    def do_export(self) -> None:
        m = self.model
        if self.busy or m.folder is None or m.archived_view or not m.order:
            return
        dlg = dialogs.ExportDialog(self, self.config, m.total)
        if dlg.exec() != dialogs.QDialog.DialogCode.Accepted:
            return
        options = dlg.options()
        self.config.save(self.config_path)
        out = dialogs.choose_export_folder(self, str(m.folder.parent))
        if out is None:
            return
        order = list(m.order)

        def job(et: md.ExifToolSession, report: Callable[[int, int, str], None]) -> export.ExportSummary:
            metas = actions.read_batch(et, order)
            return export.export_photos(et, [metas[p] for p in order], out, options, report)

        prog = QProgressDialog("Exporting photos…", "", 0, len(order), self)
        prog.setCancelButton(None)
        prog.setWindowModality(Qt.WindowModality.WindowModal)
        prog.setMinimumDuration(0)

        def progress(job_id: int, i: int, n: int, name: str) -> None:
            prog.setMaximum(n)
            prog.setValue(i)
            prog.setLabelText(f"Exporting {name} ({i} of {n})")

        self.worker.progress.connect(progress)

        def done(summary: export.ExportSummary | None, exc: BaseException | None) -> None:
            self.worker.progress.disconnect(progress)
            prog.close()
            if exc is not None or summary is None:
                self.show_status(f"Export failed: {exc}", error=True)
                return
            self.show_status(f"Exported {len(summary.exported)} photos to {out}")
            dialogs.export_summary(self, out, summary)

        self.run_job(job, done)

    def open_settings(self) -> None:
        if dialogs.SettingsDialog(self, self.config).exec():
            self.config.save(self.config_path)

    def open_log_folder(self) -> None:
        folder = config_dir() / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "About Photo Review",
            f"<b>Photo Review</b> {__version__}<br>ExifTool {self.exiftool_version or 'not running'}<br><br>"
            "Curate a folder of photos: keep or archive each one, and edit its caption, tags and date.",
        )

    # ================================================================ keyboard

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if event.type() != QEvent.Type.KeyPress or not self.isActiveWindow():
            return False
        assert isinstance(event, QKeyEvent)
        focus = QApplication.focusWidget()
        in_text = isinstance(focus, QLineEdit)
        key = event.key()
        if in_text:
            if key == Qt.Key.Key_Escape:
                self.view.setFocus()  # Esc leaves any text field
                return True
            return False  # typing, arrows and Ctrl/Cmd+Z belong to the text field
        if QApplication.activeModalWidget() is not None:
            return False
        actions_map: dict[Any, Callable[[], None]] = {
            Qt.Key.Key_Y: self.do_keep,
            Qt.Key.Key_N: self.do_archive,
            Qt.Key.Key_Right: self.go_next,
            Qt.Key.Key_Left: self.go_back,
            Qt.Key.Key_I: self.toggle_info,
            Qt.Key.Key_F5: self.refresh_folder,
        }
        is_undo = event.matches(QKeySequence.StandardKey.Undo)
        plain = event.modifiers() in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.KeypadModifier)
        if not is_undo and (key not in actions_map or not plain):
            return False
        if event.isAutoRepeat():
            return True  # ignore key auto-repeat
        (self.do_undo if is_undo else actions_map[key])()
        return True

    # ================================================================ close

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        self.config.window_geometry = bytes(self.saveGeometry().toBase64().data()).decode()
        self.config.save(self.config_path)
        QApplication.instance().removeEventFilter(self)  # type: ignore[union-attr]
        self.worker.stop()
        self.decoder.pool.waitForDone(3000)
        super().closeEvent(event)

"""Main window."""

from __future__ import annotations

import functools
import traceback
from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QAction, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from . import exif, fileops
from .config import Config
from .fileops import FileOpError
from .imaging import MAX_CAPTION_LEN
from .loader import ImageLoader
from .session import (
    DEFAULT_SORT, SORT_KEYS, SORT_OPTIONS, NoAction, ReviewSession, sort_needs_dates,
)
from .widgets import ImageView, arrow_icon, no_icon, undo_icon, yes_icon

FINISHED_TEXT = "Finished! You have reached the last photo."
ALL_DONE_TEXT = "Finished! All photos in this folder have been reviewed.\n\nUse Undo or Select Folder to continue."
EMPTY_TEXT = "No photos found in this folder.\n\nSupported: JPG, JPEG, PNG, HEIC, HEIF."
WELCOME_TEXT = "Welcome!\n\nClick \"Select Folder\" to choose a folder of photos to review."


def safe(fn):
    """Never let an exception escape a UI slot: report it in the status bar instead."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except FileOpError as exc:
            self.show_status(str(exc), error=True)
        except Exception as exc:  # pragma: no cover - last-resort safety net
            traceback.print_exc()
            self.show_status(f"Unexpected error: {exc}", error=True)
        finally:
            try:
                self.refresh_controls()
            except Exception:
                traceback.print_exc()
    return wrapper


def native(seq: QKeySequence | QKeySequence.StandardKey | str) -> str:
    return QKeySequence(seq).toString(QKeySequence.SequenceFormat.NativeText)


class MainWindow(QMainWindow):
    def __init__(self, config: Config | None = None, config_path: Path | None = None):
        super().__init__()
        self.config_path = config_path
        self.config = config or Config.load(config_path)
        if self.config.sort_key not in SORT_KEYS:
            self.config.sort_key = DEFAULT_SORT
        self.session: ReviewSession | None = None
        self.loader = ImageLoader(self)
        self.loader.ready.connect(self._on_image_ready)
        self.loader.dates_progress.connect(self._on_dates_progress)
        self.loader.dates_done.connect(self._on_dates_done)
        self._generation = 0          # bumps on each folder load / date scan
        self._busy = False            # reading EXIF dates
        self._pending_sort: str | None = None
        self._reset_position = False  # go to the first photo once dates arrive

        self.setWindowTitle("Photo Review")
        self.resize(1100, 820)
        self.setMinimumSize(720, 560)
        self._build_ui()
        self._build_shortcuts()
        if self.config.window_geometry:
            try:
                self.restoreGeometry(QByteArray.fromBase64(self.config.window_geometry.encode()))
            except Exception:
                pass

        folder = Path(self.config.last_folder) if self.config.last_folder else None
        if folder and folder.is_dir():
            self.open_folder(folder)
        else:
            self.view.set_message(WELCOME_TEXT)
            self.refresh_controls()
        self.view.setFocus()  # so Y / N / arrows work right away, not inside a text box

    # ------------------------------------------------------------ layout

    def _big_button(self, icon, text: str, tip: str, min_w: int = 150) -> QPushButton:
        b = QPushButton(icon, text)
        b.setIconSize(QSize(44, 44))
        b.setMinimumSize(min_w, 64)
        b.setToolTip(tip)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        f = b.font()
        f.setPointSize(f.pointSize() + 4)
        f.setBold(True)
        b.setFont(f)
        return b

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 8, 10, 4)
        root.setSpacing(8)
        bigger = QFont(self.font())
        bigger.setPointSize(bigger.pointSize() + 2)

        # Top bar
        top = QHBoxLayout()
        self.select_btn = QPushButton("Select Folder…")
        self.select_btn.setMinimumHeight(38)
        self.select_btn.setFont(bigger)
        self.select_btn.setToolTip("Choose the folder of photos to review")
        self.select_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.select_btn.clicked.connect(self.choose_folder)
        self.folder_label = QLabel("No folder selected")
        self.folder_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.sort_combo = QComboBox()
        for key, label in SORT_OPTIONS:
            self.sort_combo.addItem(label, key)
        self.sort_combo.setCurrentIndex(SORT_KEYS.index(self.config.sort_key))
        self.sort_combo.setToolTip("Order in which photos are shown")
        self.sort_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        self.hide_no_box = QCheckBox("Hide NO photos")
        self.hide_no_box.setToolTip("Skip photos already marked NO")
        self.hide_no_box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide_no_box.toggled.connect(self._on_hide_no)
        top.addWidget(self.select_btn)
        top.addWidget(self.folder_label, 1)
        top.addWidget(QLabel("Sort:"))
        top.addWidget(self.sort_combo)
        top.addSpacing(10)
        top.addWidget(self.hide_no_box)
        i = native("I")
        self.info_btn = QPushButton(f"Info  [{i}]")
        self.info_btn.setCheckable(True)
        self.info_btn.setToolTip(f"Show or hide camera details ({i})")
        self.info_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.info_btn.toggled.connect(self._on_info_toggled)
        top.addSpacing(10)
        top.addWidget(self.info_btn)
        root.addLayout(top)

        # Keep folder row
        keep_row = QHBoxLayout()
        keep_row.addWidget(QLabel("Keep folder:"))
        self.keep_edit = QLineEdit(self.config.keep_folder)
        self.keep_edit.setPlaceholderText("Folder where YES photos are moved")
        self.keep_edit.setToolTip("YES photos are moved here. Created automatically if missing.")
        self.keep_edit.editingFinished.connect(self._on_keep_edited)
        self.keep_browse = QPushButton("Browse…")
        self.keep_browse.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.keep_browse.clicked.connect(self.choose_keep_folder)
        keep_row.addWidget(self.keep_edit, 1)
        keep_row.addWidget(self.keep_browse)
        root.addLayout(keep_row)

        # Finished banner + image
        self.banner = QLabel()
        self.banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.banner.setStyleSheet("background:#2e7d32;color:white;padding:6px;border-radius:4px;")
        self.banner.setFont(bigger)
        self.banner.hide()
        root.addWidget(self.banner)
        self.view = ImageView()
        self.info_panel = self._build_info_panel()
        self.info_panel.hide()
        middle = QHBoxLayout()
        middle.addWidget(self.view, 1)
        middle.addWidget(self.info_panel)
        root.addLayout(middle, 1)

        # Info strip: date, filename, dimensions, size
        self.info_strip = QLabel()
        self.info_strip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.info_strip.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.info_strip.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.info_strip.setStyleSheet("color:#616161;")
        root.addWidget(self.info_strip)

        # Counter
        self.counter = QLabel("0 of 0")
        self.counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cf = QFont(self.font())
        cf.setPointSize(cf.pointSize() + 8)
        cf.setBold(True)
        self.counter.setFont(cf)
        root.addWidget(self.counter)

        # Bottom controls
        y, n = native("Y"), native("N")
        left, right = native(Qt.Key.Key_Left), native(Qt.Key.Key_Right)
        undo_key = native(QKeySequence.StandardKey.Undo)
        self.back_btn = self._big_button(arrow_icon(True), f"Back  [{left}]",
                                         f"Previous photo ({left})")
        self.no_btn = self._big_button(no_icon(), f"NO  [{n}]",
                                       f"Reject: rename with _NO and go to next ({n})")
        self.yes_btn = self._big_button(yes_icon(), f"YES  [{y}]",
                                        f"Keep: move to the Keep folder ({y})")
        self.next_btn = self._big_button(arrow_icon(False), f"Next  [{right}]",
                                         f"Next photo ({right})")
        self.undo_btn = self._big_button(undo_icon(), f"Undo  [{undo_key}]",
                                         f"Undo the last action ({undo_key})", 130)
        self.no_btn.setStyleSheet("QPushButton{color:#b71c1c;}")
        self.yes_btn.setStyleSheet("QPushButton{color:#1b5e20;}")
        self.back_btn.clicked.connect(self.go_back)
        self.no_btn.clicked.connect(self.do_no)
        self.yes_btn.clicked.connect(self.do_yes)
        self.next_btn.clicked.connect(self.go_next)
        self.undo_btn.clicked.connect(self.do_undo)
        controls = QHBoxLayout()
        controls.addStretch(1)
        for b in (self.back_btn, self.no_btn, self.yes_btn, self.next_btn):
            controls.addWidget(b)
        controls.addSpacing(30)
        controls.addWidget(self.undo_btn)
        controls.addStretch(1)
        root.addLayout(controls)

        # Caption row
        cap = QHBoxLayout()
        cap.addWidget(QLabel("Caption:"))
        self.caption_edit = QLineEdit()
        self.caption_edit.setMaxLength(MAX_CAPTION_LEN)
        self.caption_edit.setFont(bigger)
        self.caption_edit.setPlaceholderText("Type a caption, then press Submit (or Enter)")
        self.caption_edit.setToolTip(
            "Saves a COPY of the photo with this caption in a black bar. "
            "The original is not changed. Press Esc to leave the box.")
        self.caption_edit.textChanged.connect(self._update_remaining)
        self.caption_edit.returnPressed.connect(self.do_caption)
        self.remaining = QLabel()
        self.remaining.setMinimumWidth(70)
        self.caption_btn = QPushButton("Submit")
        self.caption_btn.setMinimumHeight(36)
        self.caption_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.caption_btn.setToolTip("Create the captioned copy (Enter)")
        self.caption_btn.clicked.connect(self.do_caption)
        cap.addWidget(self.caption_edit, 1)
        cap.addWidget(self.remaining)
        cap.addWidget(self.caption_btn)
        root.addLayout(cap)
        self._update_remaining()

        self.status_label = QLabel()
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.statusBar().addWidget(self.status_label, 1)
        self.setCentralWidget(central)
        self._info: tuple[Path | None, exif.PhotoInfo | None] = (None, None)
        self._set_info(None, None)

    def _build_info_panel(self) -> QFrame:
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        panel.setFixedWidth(260)
        box = QVBoxLayout(panel)
        title = QLabel("Photo info")
        tf = title.font()
        tf.setBold(True)
        tf.setPointSize(tf.pointSize() + 2)
        title.setFont(tf)
        box.addWidget(title)
        self.no_exif_label = QLabel(exif.NO_EXIF_TEXT)
        box.addWidget(self.no_exif_label)

        self.info_form = QFormLayout()
        self.info_values: dict[str, QLabel] = {}
        for key, label in [("camera", "Camera"), ("gps", "GPS"), ("shutter", "Shutter"),
                           ("aperture", "Aperture"), ("iso", "ISO"), ("focal", "Focal length")]:
            value = QLabel(exif.NA)
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.info_values[key] = value
            if key == "gps":
                self.maps_link = QLabel()
                self.maps_link.setOpenExternalLinks(True)  # opens the default browser
                self.maps_link.setToolTip("Open this location in the default web browser")
                cell = QWidget()
                col = QVBoxLayout(cell)
                col.setContentsMargins(0, 0, 0, 0)
                col.addWidget(value)
                col.addWidget(self.maps_link)
                self.info_form.addRow(f"{label}:", cell)
                self._gps_row = self.info_form.rowCount() - 1
            else:
                self.info_form.addRow(f"{label}:", value)
        box.addLayout(self.info_form)
        box.addStretch(1)

        self.show_gps_box = QCheckBox("Show GPS")
        self.show_gps_box.setToolTip("Show GPS coordinates and a map link in this panel")
        self.show_gps_box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.show_gps_box.setChecked(self.config.show_gps)
        self.show_gps_box.toggled.connect(self._on_show_gps)
        box.addWidget(self.show_gps_box)
        return panel

    def _build_shortcuts(self) -> None:
        bindings = [
            ("Y", self.do_yes), ("N", self.do_no), ("I", self.info_btn.toggle),
            (Qt.Key.Key_Right, self.go_next), (Qt.Key.Key_Left, self.go_back),
            (QKeySequence.StandardKey.Undo, self.do_undo),
        ]
        self._shortcuts = []
        for key, slot in bindings:
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(functools.partial(self._shortcut, slot))
            self._shortcuts.append(sc)
        esc = QAction(self.caption_edit)
        esc.setShortcut(QKeySequence(Qt.Key.Key_Escape))
        esc.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        esc.triggered.connect(lambda: self.view.setFocus())
        self.caption_edit.addAction(esc)
        keep_esc = QAction(self.keep_edit)
        keep_esc.setShortcut(QKeySequence(Qt.Key.Key_Escape))
        keep_esc.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        keep_esc.triggered.connect(lambda: self.view.setFocus())
        self.keep_edit.addAction(keep_esc)

    def _shortcut(self, slot) -> None:
        # Text boxes keep their keys (QLineEdit normally claims them first; this is a backstop).
        if isinstance(QApplication.focusWidget(), QLineEdit):
            return
        slot()

    # ------------------------------------------------------------ status / refresh

    def show_status(self, text: str, error: bool = False) -> None:
        self.status_label.setStyleSheet("color:#c62828;font-weight:bold;" if error else "")
        self.status_label.setText(text)
        self.status_label.setToolTip(text)

    def show_banner(self, text: str | None) -> None:
        if text:
            self.banner.setText(text)
            self.banner.show()
        else:
            self.banner.hide()

    def refresh_controls(self) -> None:
        s = self.session
        has = bool(s and s.current is not None) and not self._busy
        self.counter.setText(f"{s.position} of {s.total}" if s else "0 of 0")
        self.back_btn.setEnabled(has and s.can_back())
        self.next_btn.setEnabled(has and s.can_next())
        self.yes_btn.setEnabled(has)
        self.no_btn.setEnabled(has)
        self.caption_edit.setEnabled(has)
        self.caption_btn.setEnabled(has)
        self.undo_btn.setEnabled(bool(s and s.can_undo()) and not self._busy)
        self.sort_combo.setEnabled(not self._busy)
        self.hide_no_box.setEnabled(s is not None and not self._busy)

    def _set_info(self, path: Path | None, info: exif.PhotoInfo | None) -> None:
        """Fill the info strip and side panel. info=None means not loaded yet (or no photo)."""
        self._info = (path, info)
        if info is not None:
            self.info_strip.setText(exif.info_line(info, path.name))
        else:
            self.info_strip.setText(path.name if path else "")
        has_exif = bool(info and info.has_exif)
        self.no_exif_label.setText(exif.NO_EXIF_TEXT if info else "No photo")
        self.no_exif_label.setVisible(not has_exif)
        for row in range(self.info_form.rowCount()):
            self.info_form.setRowVisible(row, has_exif)
        if not has_exif:
            return
        self.info_form.setRowVisible(self._gps_row, self.config.show_gps)
        v = self.info_values
        v["camera"].setText(exif.format_camera(info))
        v["gps"].setText(exif.format_gps(info.gps))
        self.maps_link.setText(f'<a href="{exif.maps_url(info.gps)}">Open in Maps</a>'
                               if info.gps else "")
        self.maps_link.setVisible(info.gps is not None)
        v["shutter"].setText(exif.format_shutter(info.exposure_time))
        v["aperture"].setText(exif.format_aperture(info.f_number))
        v["iso"].setText(exif.format_iso(info.iso))
        v["focal"].setText(exif.format_focal(info.focal_length))

    def show_current(self) -> None:
        """Display the current photo (from cache or start decoding) and prefetch neighbours."""
        s = self.session
        self.caption_edit.clear()
        self._set_info(s.current if s and not self._busy else None, None)
        if s is None:
            self.view.set_message(WELCOME_TEXT)
            return
        if self._busy:
            return
        cur = s.current
        if cur is None:
            self.view.set_message(ALL_DONE_TEXT if s.undo_stack else EMPTY_TEXT)
            if s.undo_stack:
                self.show_banner(None)
            return
        cached = self.loader.get(cur)
        if cached is not None:
            self._display(cur, *cached)
        else:
            self.view.set_message(f"Loading {cur.name}…", no_badge=fileops.is_no(cur))
            self.loader.request(cur)
        self.loader.request(s.peek(1))
        self.loader.request(s.peek(-1))

    def _display(self, path: Path, img, error: str, info=None) -> None:
        self._set_info(path, info)
        if img is None:
            self.view.set_message(f"Cannot display this photo:\n{path.name}",
                                  no_badge=fileops.is_no(path))
            self.show_status(f"Cannot open {path.name}: {error}", error=True)
        else:
            self.view.set_image(img, no_badge=fileops.is_no(path))
        self.setWindowTitle(f"Photo Review - {path.name}")

    def _on_image_ready(self, path: Path, img, error: str, info) -> None:
        if self.session and not self._busy and path == self.session.current:
            self._display(path, img, error, info)

    def _after_move(self, show_finished: bool = False) -> None:
        self.show_banner(FINISHED_TEXT if show_finished and self.session.current else None)
        self.show_current()

    # ------------------------------------------------------------ folder / keep

    @safe
    def choose_folder(self) -> None:
        start = self.config.last_folder or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Select photo folder", start)
        if path:
            self.open_folder(Path(path))

    @safe
    def open_folder(self, folder: Path) -> None:
        if self.session:
            self.session.close()
        self.loader.clear()
        self.show_banner(None)
        self.folder_label.setText(str(folder))
        self.folder_label.setToolTip(str(folder))
        if not self.keep_edit.text().strip():
            self.keep_edit.setText(str(folder / "Keep"))
        self.config.last_folder = str(folder)
        self.config.keep_folder = self.keep_edit.text().strip()
        self.config.save(self.config_path)

        sort_key = self.sort_combo.currentData()
        self.session = ReviewSession(folder, self._keep_path(), sort_key,
                                     self.hide_no_box.isChecked())
        self._generation += 1
        self._busy = False
        try:
            # Scan in name order first; date sorts are applied once EXIF is read in the background.
            self.session.sort_key = "name_asc"
            self.session.load()
            self.session.sort_key = sort_key
        except FileOpError:
            self.view.set_message(f"Cannot read this folder:\n{folder}")
            raise
        self.show_status(f"Loaded {len(self.session.files)} photos from {folder}")
        self.view.setFocus()
        self._check_keep(quiet=True)
        if sort_needs_dates(sort_key) and self.session.files:
            self._reset_position = True
            self._start_date_scan(sort_key)
        else:
            self.session.set_sort(sort_key)
            self.session.index = 0
            self.show_current()

    def _keep_path(self) -> Path | None:
        text = self.keep_edit.text().strip()
        return Path(text).expanduser() if text else None

    def _check_keep(self, quiet: bool = False) -> bool:
        source = self.session.folder if self.session else None
        error = fileops.validate_keep_folder(self._keep_path(), source)
        self.keep_edit.setStyleSheet("QLineEdit{border:2px solid #c62828;}" if error else "")
        if error and not quiet:
            self.show_status(error, error=True)
        elif error:
            self.keep_edit.setToolTip(error)
        else:
            self.keep_edit.setToolTip("YES photos are moved here. Created automatically if missing.")
        return error is None

    @safe
    def _on_keep_edited(self) -> None:
        self.config.keep_folder = self.keep_edit.text().strip()
        self.config.save(self.config_path)
        if self.session:
            self.session.keep_folder = self._keep_path()
        if self._check_keep():
            self.show_status(f"Keep folder: {self._keep_path()}")

    @safe
    def choose_keep_folder(self) -> None:
        start = self.keep_edit.text().strip() or self.config.last_folder or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Select Keep folder", start)
        if path:
            self.keep_edit.setText(path)
            self._on_keep_edited()

    # ------------------------------------------------------------ sort / filter

    @safe
    def _on_sort_changed(self, _index: int) -> None:
        key = self.sort_combo.currentData()
        self.config.sort_key = key
        self.config.save(self.config_path)
        if not self.session:
            return
        missing = [p for p in self.session.files if p not in self.session.date_cache]
        if sort_needs_dates(key) and missing:
            self._start_date_scan(key, missing)
        else:
            self.session.set_sort(key)
            self.show_current()

    def _start_date_scan(self, key: str, paths: list[Path] | None = None) -> None:
        self._generation += 1
        self._busy = True
        self._pending_sort = key
        paths = paths if paths is not None else list(self.session.files)
        self.view.set_message(f"Reading photo dates… (0 of {len(paths)})")
        self._set_info(None, None)
        self.show_status("Reading photo dates for sorting…")
        self.loader.scan_dates(self._generation, paths)
        self.refresh_controls()

    def _on_dates_progress(self, gen: int, done: int, total: int) -> None:
        if gen == self._generation and self._busy:
            self.view.set_message(f"Reading photo dates… ({done} of {total})")

    @safe
    def _on_dates_done(self, gen: int, dates: dict) -> None:
        if gen != self._generation or not self.session:
            return
        self.session.date_cache.update(dates)
        self._busy = False
        self.session.set_sort(self._pending_sort or self.session.sort_key)
        if self._reset_position:
            self.session.index = 0
            self._reset_position = False
        self.show_status(f"Sorted by {self.sort_combo.currentText().lower()}")
        self.show_current()

    @safe
    def _on_hide_no(self, checked: bool) -> None:
        if self.session:
            self.session.set_hide_no(checked)
            self.show_banner(None)
            self.show_current()

    # ------------------------------------------------------------ info panel

    def _on_info_toggled(self, checked: bool) -> None:
        self.info_panel.setVisible(checked)

    def _on_show_gps(self, checked: bool) -> None:
        self.config.show_gps = checked
        self.config.save(self.config_path)
        self._set_info(*self._info)

    # ------------------------------------------------------------ actions

    def _ready(self) -> bool:
        return bool(self.session and self.session.current is not None and not self._busy)

    @safe
    def go_next(self) -> None:
        if self._ready() and self.session.can_next():
            self.session.next()
            self._after_move()

    @safe
    def go_back(self) -> None:
        if self._ready() and self.session.can_back():
            self.session.back()
            self._after_move()

    @safe
    def do_yes(self) -> None:
        if not self._ready():
            return
        if not self._check_keep():
            return
        self.session.keep_folder = self._keep_path()
        result = self.session.mark_yes()
        self.show_status(result.message, error="Warning" in result.message)
        self._after_move(result.finished)

    @safe
    def do_no(self) -> None:
        if not self._ready():
            return
        result = self.session.mark_no()
        self.show_status(result.message)
        self._after_move(result.finished)

    @safe
    def do_caption(self) -> None:
        if not self._ready():
            return
        text = self.caption_edit.text()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = self.session.add_caption(text)
        finally:
            QApplication.restoreOverrideCursor()
        self.caption_edit.clear()
        self.view.setFocus()
        self.show_status(result.message)

    @safe
    def do_undo(self) -> None:
        if not self.session or self._busy or not self.session.can_undo():
            return
        top = self.session.undo_stack[-1]
        result = self.session.undo()
        if isinstance(top, NoAction):
            self.loader.rename(top.new, top.old)
        self.show_status(result.message, error="Warning" in result.message)
        self._after_move()

    # ------------------------------------------------------------ misc

    def _update_remaining(self) -> None:
        left = MAX_CAPTION_LEN - len(self.caption_edit.text())
        self.remaining.setText(f"{left} left")
        self.remaining.setStyleSheet("color:#c62828;" if left <= 5 else "")

    def closeEvent(self, event) -> None:
        self.config.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        self.config.keep_folder = self.keep_edit.text().strip()
        self.config.sort_key = self.sort_combo.currentData()
        self.config.save(self.config_path)
        self._generation += 1
        self.loader.pool.waitForDone(3000)
        if self.session:
            self.session.close()
        super().closeEvent(event)

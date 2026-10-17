"""Main window."""

from __future__ import annotations

import functools
import traceback
from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QAction, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from . import fileops, metadata
from .config import Config
from .fileops import FileOpError
from .loader import ImageLoader
from .session import (
    DEFAULT_SORT, SORT_KEYS, SORT_OPTIONS, CaptionAction, NoAction, ReviewSession,
    sort_needs_dates,
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
        self._cap_path: Path | None = None    # photo the caption box belongs to
        self._cap_saved: str | None = None    # its stored caption (None until metadata is read)
        self._note: tuple[str, bool] | None = None  # caption save result shown with the next status

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
        root.addWidget(self.view, 1)

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
        self.caption_edit.setMaxLength(metadata.MAX_CAPTION_BYTES)
        self.caption_edit.setFont(bigger)
        self.caption_edit.setPlaceholderText("Type a caption, then press Submit (or Enter)")
        self.caption_edit.setToolTip(
            "The photo's caption. It is saved into the photo's caption fields and into a "
            "captioned COPY (with a black caption bar) when you press Submit or move to another "
            "photo. Clear the box to remove the caption. Press Esc to leave the box.")
        self.caption_edit.textChanged.connect(self._on_caption_text)
        self.caption_edit.returnPressed.connect(self.do_caption)
        self.remaining = QLabel()
        self.remaining.setMinimumWidth(80)
        self.remaining.setToolTip("Space left (accented letters and emoji take more than one)")
        self.disagree = QLabel("⚠ Caption fields differ")
        self.disagree.setStyleSheet("color:#b26a00;font-weight:bold;")
        self.disagree.hide()
        self.caption_btn = QPushButton("Submit")
        self.caption_btn.setMinimumHeight(36)
        self.caption_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.caption_btn.setToolTip("Save the caption into the photo and create the captioned copy (Enter)")
        self.caption_btn.clicked.connect(self.do_caption)
        cap.addWidget(self.caption_edit, 1)
        cap.addWidget(self.disagree)
        cap.addWidget(self.remaining)
        cap.addWidget(self.caption_btn)
        root.addLayout(cap)
        self._on_caption_text("")

        self.status_label = QLabel()
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.statusBar().addWidget(self.status_label, 1)
        self.setCentralWidget(central)

    def _build_shortcuts(self) -> None:
        bindings = [
            ("Y", self.do_yes), ("N", self.do_no),
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
        if self._note:
            note, note_error = self._note
            self._note = None
            text, error = f"{note}  |  {text}", error or note_error
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

    def show_current(self) -> None:
        """Display the current photo (from cache or start decoding) and prefetch neighbours."""
        s = self.session
        if s is None:
            self._set_caption_box(None, None)
            self.view.set_message(WELCOME_TEXT)
            return
        if self._busy:
            return
        cur = s.current
        if cur is None:
            self._set_caption_box(None, None)
            self.view.set_message(ALL_DONE_TEXT if s.undo_stack else EMPTY_TEXT)
            if s.undo_stack:
                self.show_banner(None)
            return
        cached = self.loader.get(cur)
        if cached is not None:
            img, error, fields = cached
            self._display(cur, img, error)
            self._set_caption_box(cur, fields)
        else:
            self._set_caption_box(cur, None)
            self.view.set_message(f"Loading {cur.name}…", no_badge=fileops.is_no(cur))
            self.loader.request(cur)
        self.loader.request(s.peek(1))
        self.loader.request(s.peek(-1))

    def _display(self, path: Path, img, error: str) -> None:
        if img is None:
            self.view.set_message(f"Cannot display this photo:\n{path.name}",
                                  no_badge=fileops.is_no(path))
            self.show_status(f"Cannot open {path.name}: {error}", error=True)
        else:
            self.view.set_image(img, no_badge=fileops.is_no(path))
        self.setWindowTitle(f"Photo Review - {path.name}")

    def _on_image_ready(self, path: Path, img, error: str, fields) -> None:
        if self.session and not self._busy and path == self.session.current:
            self._display(path, img, error)
            self._set_caption_box(path, fields)

    # ------------------------------------------------------------ caption box

    def _set_caption_box(self, path: Path | None, fields) -> None:
        """Show `path`'s stored caption in the box (once its metadata has been read)."""
        if path != self._cap_path:
            self._cap_path, self._cap_saved = path, None
            self.caption_edit.setText("")
            self.disagree.hide()
        if path is None or fields is None or self._cap_saved is not None:
            return
        choice = metadata.choose_caption(fields)
        self._cap_saved = choice.text
        if not self.caption_edit.text():  # don't overwrite what the user already typed
            self.caption_edit.setText(choice.text)
            self.caption_edit.setCursorPosition(len(choice.text))
        if choice.disagree:
            detail = "\n".join(f"{metadata.FIELD_LABELS[k]}: {v or '(blank)'}"
                               for k, v in fields.values().items())
            self.disagree.setText("⚠ Fields differ")
            self.disagree.setToolTip(
                f"The photo's caption fields hold different text. Showing "
                f"{metadata.FIELD_LABELS[choice.source]}. Submit saves the box's text to all "
                f"of them.\n\n" + detail)
            self.disagree.show()
        else:
            self.disagree.hide()
        if fields.errors:
            self.show_status(f"Caption metadata could not be fully read: {'; '.join(fields.errors)}",
                             error=True)

    def _caption_dirty(self) -> bool:
        if self._cap_path is None:
            return False
        return metadata.clean(self.caption_edit.text()) != (self._cap_saved or "")

    def _save_caption(self) -> bool:
        """Save the box's caption for its photo. Returns True if something was attempted."""
        path, s = self._cap_path, self.session
        if s is None or path is None or path not in s.files:
            return False
        text = metadata.clean(self.caption_edit.text())
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = s.save_caption(text, photo=path)
        finally:
            QApplication.restoreOverrideCursor()
        self._cap_saved = text
        self.disagree.hide()
        self.loader.set_fields(path, metadata.read_caption_fields(path))
        self.show_status(result.message, error="Warning" in result.message)
        return True

    def _commit_caption(self) -> None:
        """Before the displayed photo changes: save an edited or cleared caption."""
        if not self._caption_dirty():
            return
        try:
            self._save_caption()
            self._note = (self.status_label.text(), "Warning" in self.status_label.text())
        except FileOpError as exc:
            self._note = (f"Caption not saved: {exc}", True)
        self._cap_path = None  # the box now belongs to whatever is shown next

    def _on_caption_text(self, text: str) -> None:
        if metadata.caption_bytes(text) > metadata.MAX_CAPTION_BYTES:
            while metadata.caption_bytes(text) > metadata.MAX_CAPTION_BYTES:
                text = text[:-1]
            self.caption_edit.setText(text)  # re-enters this handler once with valid text
            return
        left = metadata.MAX_CAPTION_BYTES - metadata.caption_bytes(text)
        self.remaining.setText(f"{left} left")
        self.remaining.setStyleSheet("color:#c62828;" if left <= 20 else "")

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
            self._commit_caption()
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
        self._commit_caption()
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
            self._commit_caption()
            self.session.set_hide_no(checked)
            self.show_banner(None)
            self.show_current()

    # ------------------------------------------------------------ actions

    def _ready(self) -> bool:
        return bool(self.session and self.session.current is not None and not self._busy)

    @safe
    def go_next(self) -> None:
        if self._ready() and self.session.can_next():
            self._commit_caption()
            self.session.next()
            self._after_move()

    @safe
    def go_back(self) -> None:
        if self._ready() and self.session.can_back():
            self._commit_caption()
            self.session.back()
            self._after_move()

    @safe
    def do_yes(self) -> None:
        if not self._ready():
            return
        if not self._check_keep():
            return
        self._commit_caption()  # save first so the captioned copy moves with the photo
        self.session.keep_folder = self._keep_path()
        result = self.session.mark_yes()
        self.show_status(result.message, error="Warning" in result.message)
        self._after_move(result.finished)

    @safe
    def do_no(self) -> None:
        if not self._ready():
            return
        self._commit_caption()
        result = self.session.mark_no()
        self.show_status(result.message)
        self._after_move(result.finished)

    @safe
    def do_caption(self) -> None:
        if not self._ready():
            return
        text = metadata.clean(self.caption_edit.text())
        if not text and not self._cap_saved:
            raise FileOpError("Type a caption first.")
        self._cap_path = self.session.current
        self._save_caption()
        self.caption_edit.setText(text)
        self.view.setFocus()

    @safe
    def do_undo(self) -> None:
        if not self.session or self._busy:
            return
        if self._caption_dirty():  # first undo discards an unsaved caption edit
            self.caption_edit.setText(self._cap_saved or "")
            self.show_status("Caption edit discarded")
            return
        if not self.session.can_undo():
            return
        top = self.session.undo_stack[-1]
        result = self.session.undo()
        if isinstance(top, NoAction):
            self.loader.rename(top.new, top.old)
        elif isinstance(top, CaptionAction):
            self.loader.set_fields(top.photo, metadata.read_caption_fields(top.photo))
        self._cap_path = None  # refill the caption box from the photo's metadata
        self.show_status(result.message, error="Warning" in result.message)
        self._after_move()

    # ------------------------------------------------------------ misc

    def closeEvent(self, event) -> None:
        try:
            self._commit_caption()
        except Exception:
            traceback.print_exc()
        self.config.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        self.config.keep_folder = self.keep_edit.text().strip()
        self.config.sort_key = self.sort_combo.currentData()
        self.config.save(self.config_path)
        self._generation += 1
        self.loader.pool.waitForDone(3000)
        if self.session:
            self.session.close()
        super().closeEvent(event)

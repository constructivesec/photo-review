"""Main window, driven offscreen with the real worker and ExifTool."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from photo_review import metadata as md
from photo_review.config import Config
from photo_review.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


@pytest.fixture
def folder(fixture_dir: Path, tmp_path: Path) -> Path:
    f = tmp_path / "photos"
    f.mkdir()
    for n in ["lightroom.jpg", "june_1962.jpg", "no_date.jpg", "iphone.heic"]:
        shutil.copy2(fixture_dir / n, f / n)
    return f


def settle(w: MainWindow, timeout_ms: int = 8000) -> None:
    """Wait until the worker is idle and the current photo's metadata is shown."""
    for _ in range(timeout_ms // 20):
        QTest.qWait(20)
        idle = w._ready and not w._jobs and not w.busy
        cur = w.model.current
        if idle and (cur is None or w.model.meta(cur) is not None):
            QTest.qWait(30)
            return
    raise AssertionError(f"window did not settle: {w.status.text()}")


@pytest.fixture
def window(app: QApplication, folder: Path, tmp_path: Path) -> Iterator[MainWindow]:
    cfg = Config(last_folder=str(folder), first_launch_notice_dismissed=True)
    w = MainWindow(cfg, tmp_path / "config.json")
    w.show()
    w.activateWindow()
    settle(w)
    yield w
    dispose(w)


def dispose(w: MainWindow) -> None:
    """Close and delete the window now, so no Qt object outlives the QApplication at exit."""
    w.close()
    w.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


def key(
    w: MainWindow, k: Qt.Key, mods: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier, repeat: bool = False
) -> None:
    target = QApplication.focusWidget() or w
    for t in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
        QApplication.sendEvent(target, QKeyEvent(t, k, mods, "", repeat))
    settle(w)


def rating(path: Path) -> int | None:
    with md.ExifToolSession() as et:
        return md.read_meta(et, [path])[path].rating


def test_loads_folder(window: MainWindow) -> None:
    assert window.counter.text() == "1 of 4" and window.model.current is not None
    assert not window.back_btn.isEnabled() and window.keep_btn.isEnabled()


def test_keyboard_keep_archive_undo(window: MainWindow, folder: Path) -> None:
    window.view.setFocus()
    first = window.model.current
    assert first is not None
    key(window, Qt.Key.Key_Y)
    assert rating(first) == 1 and window.counter.text() == "2 of 4"
    second = window.model.current
    assert second is not None
    key(window, Qt.Key.Key_N)
    assert (folder / "_archive" / second.name).exists() and window.counter.text() == "2 of 3"
    key(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert second.exists() and window.counter.text() == "2 of 4" and window.model.current == second
    key(window, Qt.Key.Key_Left)
    assert window.model.current == first
    key(window, Qt.Key.Key_Right)
    assert window.model.current == second


def test_autorepeat_ignored(window: MainWindow) -> None:
    window.view.setFocus()
    key(window, Qt.Key.Key_Right, repeat=True)
    assert window.counter.text() == "1 of 4"


def test_shortcuts_disabled_while_typing_and_esc_leaves(window: MainWindow) -> None:
    window.caption_edit.setFocus()
    QTest.keyClicks(window.caption_edit, "Yummy N")
    settle(window)
    assert window.caption_edit.text().endswith("Yummy N") and window.counter.text() == "1 of 4"
    assert window.model.undo == []
    key(window, Qt.Key.Key_Escape)
    assert QApplication.focusWidget() is window.view


def test_save_caption_returns_focus(window: MainWindow) -> None:
    cur = window.model.current
    assert cur is not None
    window.caption_edit.setFocus()
    window.caption_edit.setText("Picnic 1962")
    window.do_save_caption()
    settle(window)
    assert window.model.meta(cur).caption == "Picnic 1962"  # type: ignore[union-attr]
    assert QApplication.focusWidget() is window.view


def test_unsaved_caption_discarded_on_navigation(window: MainWindow) -> None:
    first = window.model.current
    window.caption_edit.setText("never saved")
    window.go_next()
    settle(window)
    window.go_back()
    settle(window)
    assert window.model.current == first and window.caption_edit.text() != "never saved"


def test_save_date_and_validation(window: MainWindow, folder: Path) -> None:
    window.model.go_to(folder / "no_date.jpg")
    window.show_current()
    settle(window)
    assert window.approx_box.isChecked() and window.info_nodate.isVisible()
    window.month_edit.setText("13")
    window.year_edit.setText("1962")
    window.do_save_date()
    settle(window)
    assert "1 to 12" in window.status.text() and window.model.undo == []
    window.month_edit.setText("6")
    window.do_save_date()
    settle(window)
    assert window.stored_date.text() == "Stored date: 1962-06-01 12:00:00"
    assert QApplication.focusWidget() is window.view


def test_tags_add_reject_remove(window: MainWindow) -> None:
    window.tag_edit.setText("Person:Emma")
    window.do_add_tag()
    settle(window)
    meta = window.model.meta(window.model.current)
    assert meta is not None and "person:emma" in meta.keywords
    assert "person:emma" in window.config.tag_history
    window.tag_edit.setText("status:archive")
    window.do_add_tag()
    settle(window)
    assert "managed by the app" in window.status.text()
    window.do_remove_tag("person:emma")
    settle(window)
    meta = window.model.meta(window.model.current)
    assert meta is not None and "person:emma" not in meta.keywords


def test_archived_view_and_keep_from_archive(window: MainWindow, folder: Path) -> None:
    window.view.setFocus()
    name = window.model.current.name  # type: ignore[union-attr]
    key(window, Qt.Key.Key_N)
    window.show_archived.setChecked(True)
    settle(window)
    assert window.counter.text() == "1 of 1" and not window.archive_btn.isEnabled()
    assert window.model.undo == []  # cleared on view toggle
    key(window, Qt.Key.Key_Y)
    assert (folder / name).exists() and window.counter.text() == "0 of 0"
    assert "Finished" in window.view._message
    window.show_archived.setChecked(False)
    settle(window)
    assert window.counter.text() == "1 of 4"


def test_undated_only_filter(window: MainWindow) -> None:
    window.undated_only.setChecked(True)
    settle(window)
    assert window.counter.text() == "1 of 1" and window.model.current.name == "no_date.jpg"  # type: ignore[union-attr]
    window.month_edit.setText("6")
    window.year_edit.setText("1962")
    window.do_save_date()
    settle(window)
    assert window.counter.text() == "0 of 0"
    assert "Finished" in window.view._message
    window.do_undo()
    settle(window)
    assert window.counter.text() == "1 of 1"


def test_sort_by_date_keeps_current(window: MainWindow, folder: Path) -> None:
    window.model.go_to(folder / "lightroom.jpg")
    window.show_current()
    settle(window)
    window.sort_combo.setCurrentIndex(2)  # date
    settle(window)
    assert window.model.current == folder / "lightroom.jpg"
    assert [p.name for p in window.model.order] == [
        "june_1962.jpg",
        "lightroom.jpg",
        "iphone.heic",
        "no_date.jpg",
    ]
    assert window.config.sort_key == "date"


def test_refresh_skips_externally_deleted(window: MainWindow, folder: Path) -> None:
    (folder / "no_date.jpg").unlink()
    key(window, Qt.Key.Key_F5)
    assert window.counter.text() == "1 of 3" and "no_date.jpg" in window.status.text()


def test_info_panel_toggle_and_gps(window: MainWindow, folder: Path) -> None:
    window.model.go_to(folder / "iphone.heic")
    window.show_current()
    settle(window)
    window.view.setFocus()
    key(window, Qt.Key.Key_I)
    assert window.info_dock.isVisible()
    assert window.i_device.text() == "Apple iPhone 15 Pro" and "Hidden" in window.i_gps.text()
    window.show_gps.setChecked(True)
    assert "openstreetmap.org" in window.i_gps.text() and window.config.show_gps


def test_archive_folder_selection_blocked(
    window: MainWindow, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QMessageBox

    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[1]))
    (folder / "_archive").mkdir(exist_ok=True)
    window.open_folder(folder / "_archive")
    assert shown and window.model.folder == folder


def test_busy_disables_actions(window: MainWindow) -> None:
    window.set_busy(True)
    assert not window.keep_btn.isEnabled() and not window.archive_btn.isEnabled()
    window.do_keep()  # ignored while busy
    assert window.model.undo == []
    window.set_busy(False)
    assert window.keep_btn.isEnabled()


def test_empty_folder(app: QApplication, tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    w = MainWindow(Config(last_folder=str(empty), first_launch_notice_dismissed=True), tmp_path / "c.json")
    w.show()
    settle(w)
    assert w.counter.text() == "0 of 0" and "no photos" in w.view._message and not w.keep_btn.isEnabled()
    dispose(w)

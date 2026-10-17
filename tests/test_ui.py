"""Smoke tests of the main window using Qt's offscreen platform."""

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from photo_review import metadata
from photo_review.config import Config
from photo_review.ui import MainWindow

from conftest import make_image, set_raw_fields


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, photo_dir, keep_dir, tmp_path):
    for n in ["a.jpg", "b.jpg", "c.jpg"]:
        make_image(photo_dir / n)
    cfg = Config(last_folder=str(photo_dir), keep_folder=str(keep_dir))
    w = MainWindow(cfg, tmp_path / "cfg.json")
    w.show()
    QTest.qWaitForWindowExposed(w)
    yield w
    w.close()


def test_loads_and_counts(window):
    assert window.counter.text() == "1 of 3"
    assert not window.back_btn.isEnabled() and window.next_btn.isEnabled()


def test_keyboard_actions(window, photo_dir, keep_dir):
    window.activateWindow()
    QTest.keyClick(window, Qt.Key.Key_Right)
    assert window.counter.text() == "2 of 3"
    QTest.keyClick(window, Qt.Key.Key_N)
    assert (photo_dir / "b_NO.jpg").exists()
    assert window.counter.text() == "3 of 3"
    QTest.keyClick(window, Qt.Key.Key_Y)
    assert (keep_dir / "c.jpg").exists()
    assert window.counter.text() == "2 of 2"
    QTest.keyClick(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert (photo_dir / "c.jpg").exists() and window.counter.text() == "3 of 3"


def test_shortcuts_disabled_in_caption(window, photo_dir, keep_dir):
    window.activateWindow()
    window.caption_edit.setFocus()
    QTest.keyClicks(window.caption_edit, "Yummy")
    assert window.caption_edit.text() == "Yummy"
    assert not keep_dir.exists() and window.counter.text() == "1 of 3"
    QTest.keyClick(window.caption_edit, Qt.Key.Key_Return)
    assert (photo_dir / "a_captioned.jpg").exists()
    assert window.caption_edit.text() == "Yummy"  # box shows the photo's saved caption


def test_keep_equal_source_blocks_yes(window, photo_dir):
    window.keep_edit.setText(str(photo_dir))
    window._on_keep_edited()
    window.do_yes()
    assert (photo_dir / "a.jpg").exists()
    assert "same" in window.status_label.text()


def test_image_eventually_displayed(window):
    for _ in range(60):
        if window.view._image is not None:
            break
        QTest.qWait(50)
    assert window.view._image is not None


def wait_for_caption_box(window, timeout_ms=3000):
    for _ in range(timeout_ms // 50):
        if window._cap_saved is not None:
            return
        QTest.qWait(50)


def caption_of(path):
    return metadata.choose_caption(metadata.read_caption_fields(path)).text


def test_existing_caption_prefills_box(app, photo_dir, keep_dir, tmp_path):
    p = make_image(photo_dir / "a.jpg")
    metadata.write_caption(p, "Beach day")
    w = MainWindow(Config(last_folder=str(photo_dir), keep_folder=str(keep_dir)), tmp_path / "c.json")
    w.show()
    wait_for_caption_box(w)
    assert w.caption_edit.text() == "Beach day"
    assert w.disagree.isHidden()
    w.close()


def test_edit_saved_when_moving_to_next_photo(window, photo_dir):
    wait_for_caption_box(window)
    window.caption_edit.setText("Typed then moved on")
    window.go_next()
    assert caption_of(photo_dir / "a.jpg") == "Typed then moved on"
    assert (photo_dir / "a_captioned.jpg").exists()
    assert window.caption_edit.text() == ""  # b has no caption
    window.go_back()
    wait_for_caption_box(window)
    assert window.caption_edit.text() == "Typed then moved on"


def test_clearing_caption_removes_it_on_move(window, photo_dir):
    wait_for_caption_box(window)
    metadata.write_caption(photo_dir / "a.jpg", "Old caption")
    window.loader.clear()
    window._cap_path = None
    window.show_current()
    wait_for_caption_box(window)
    assert window.caption_edit.text() == "Old caption"
    window.caption_edit.setText("")
    window.go_next()
    assert caption_of(photo_dir / "a.jpg") == ""


def test_unchanged_caption_does_not_touch_file(window, photo_dir):
    wait_for_caption_box(window)
    before = (photo_dir / "a.jpg").read_bytes()
    window.go_next()
    assert (photo_dir / "a.jpg").read_bytes() == before
    assert not (photo_dir / "a_captioned.jpg").exists()


def test_undo_discards_unsaved_edit_first(window, photo_dir):
    wait_for_caption_box(window)
    window.caption_edit.setText("never mind")
    window.do_undo()
    assert window.caption_edit.text() == ""
    assert caption_of(photo_dir / "a.jpg") == ""


def test_caption_saved_before_yes(window, photo_dir, keep_dir):
    wait_for_caption_box(window)
    window.caption_edit.setText("Keeper")
    window.do_yes()
    assert caption_of(keep_dir / "a.jpg") == "Keeper"
    assert (keep_dir / "a_captioned.jpg").exists()


def test_disagreeing_fields_show_iptc_and_notice(app, photo_dir, keep_dir, tmp_path):
    p = make_image(photo_dir / "a.jpg")
    set_raw_fields(p, desc="from exif", iptc="from iptc", xmp="from xmp")
    w = MainWindow(Config(last_folder=str(photo_dir), keep_folder=str(keep_dir)), tmp_path / "c.json")
    w.show()
    wait_for_caption_box(w)
    assert w.caption_edit.text() == "from iptc"
    assert not w.disagree.isHidden()
    w.do_caption()  # Submit syncs all fields
    assert set(metadata.read_caption_fields(p).values().values()) == {"from iptc"}
    w.close()


def test_save_during_pending_load_is_not_overwritten(window, photo_dir):
    # Type and move on before the first photo's metadata has arrived.
    window.caption_edit.setText("Fast typist")
    window.go_next()
    window.loader.pool.waitForDone(3000)
    QTest.qWait(100)
    window.go_back()
    wait_for_caption_box(window)
    assert window.caption_edit.text() == "Fast typist"

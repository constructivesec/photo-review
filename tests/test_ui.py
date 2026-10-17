"""Smoke tests of the main window using Qt's offscreen platform."""

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from photo_review.config import Config
from photo_review.ui import MainWindow

from PIL import Image

from conftest import make_image


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
    assert window.caption_edit.text() == ""


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


def wait_for_info(window):
    for _ in range(60):
        if window._info[1] is not None:
            return window._info[1]
        QTest.qWait(50)
    raise AssertionError("info never arrived")


def test_info_strip_and_toggle(window, photo_dir):
    info = wait_for_info(window)
    text = window.info_strip.text()
    assert "a.jpg" in text and "120 × 80" in text and "(modified)" in text
    assert info.date_is_modified
    assert not window.info_panel.isVisible()
    window.activateWindow()
    QTest.keyClick(window, Qt.Key.Key_I)
    assert window.info_panel.isVisible() and window.info_btn.isChecked()
    assert window.no_exif_label.isVisible() and window.no_exif_label.text() == "No EXIF data"
    QTest.keyClick(window, Qt.Key.Key_I)
    assert not window.info_panel.isVisible()


def test_info_key_types_in_caption(window):
    window.activateWindow()
    window.caption_edit.setFocus()
    QTest.keyClicks(window.caption_edit, "I")
    assert window.caption_edit.text() == "I" and not window.info_btn.isChecked()


def test_gps_row_and_setting(app, photo_dir, tmp_path):
    from PIL.TiffImagePlugin import IFDRational
    exif = Image.Exif()
    exif[0x010F] = "TestCam"
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = "N", (IFDRational(40, 1), IFDRational(0, 1), IFDRational(0, 1))
    gps[3], gps[4] = "W", (IFDRational(74, 1), IFDRational(0, 1), IFDRational(0, 1))
    make_image(photo_dir / "g.jpg", exif=exif.tobytes())
    cfg_path = tmp_path / "cfg.json"
    w = MainWindow(Config(last_folder=str(photo_dir)), cfg_path)
    w.show()
    try:
        wait_for_info(w)
        w.info_btn.setChecked(True)
        assert not w.no_exif_label.isVisible()
        assert w.info_values["camera"].text() == "TestCam"
        assert w.info_values["gps"].text() == "40.000000, -74.000000"
        assert "google.com/maps" in w.maps_link.text() and w.maps_link.isVisible()
        assert w.info_form.isRowVisible(w._gps_row)
        assert w.info_values["iso"].text() == "n/a"
        w.show_gps_box.setChecked(False)
        assert not w.info_form.isRowVisible(w._gps_row)
        assert Config.load(cfg_path).show_gps is False
    finally:
        w.close()

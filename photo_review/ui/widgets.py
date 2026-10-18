"""Custom widgets: photo view with badges, tag chips, painted icons."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QSizePolicy, QToolButton, QWidget

from .. import tag_logic

GREEN = QColor("#1b7a2e")
RED = QColor("#c62828")
AMBER = QColor("#b26a00")
SLATE = QColor("#37474f")


class PhotoView(QWidget):
    """Fit-to-window image (no zoom) with a KEEP/ARCHIVED badge and read-only stars."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(320, 220)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Photo")
        self._image: QImage | None = None
        self._scaled: QPixmap | None = None
        self._message = ""
        self._rating: int | None = None

    def show_image(self, image: QImage | None, rating: int | None) -> None:
        self._image, self._scaled, self._message, self._rating = image, None, "", rating
        self.update()

    def show_message(self, text: str, rating: int | None = None) -> None:
        self._image, self._scaled, self._message, self._rating = None, None, text, rating
        self.update()

    def resizeEvent(self, event: object) -> None:  # noqa: N802 - Qt override
        self._scaled = None
        super().resizeEvent(event)  # type: ignore[arg-type]

    def _rect(self) -> QRectF:
        assert self._image is not None
        iw, ih = self._image.width(), self._image.height()
        scale = min(self.width() / iw, self.height() / ih)
        w, h = iw * scale, ih * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def paintEvent(self, event: object) -> None:  # noqa: N802 - Qt override
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#1e1e1e"))
        area = QRectF(self.rect())
        if self._image is not None and not self._image.isNull():
            area = self._rect()
            if self._scaled is None:
                dpr = self.devicePixelRatioF()
                size = QSize(max(1, round(area.width() * dpr)), max(1, round(area.height() * dpr)))
                self._scaled = QPixmap.fromImage(
                    self._image.scaled(
                        size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                    )
                )
                self._scaled.setDevicePixelRatio(dpr)
            p.drawPixmap(area.topLeft(), self._scaled)
        elif self._message:
            p.setPen(QColor("#eeeeee"))
            f = QFont(self.font())
            f.setPointSize(f.pointSize() + 6)
            p.setFont(f)
            p.drawText(
                self.rect().adjusted(24, 24, -24, -24),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                self._message,
            )
        self._draw_badge(p, area)
        p.end()

    def _draw_badge(self, p: QPainter, area: QRectF) -> None:
        r = self._rating
        if r is None or r == 0:
            return
        text, color = ("ARCHIVED", RED) if r < 0 else ("KEEP", GREEN)
        h = max(34.0, min(64.0, min(self.width(), self.height()) * 0.08))
        f = QFont(self.font())
        f.setBold(True)
        f.setPixelSize(int(h * 0.55))
        p.setFont(f)
        w = p.fontMetrics().horizontalAdvance(text) + h * 0.8
        box = QRectF(area.left() + 12, area.top() + 12, w, h)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor("white"), 3))
        p.setBrush(color)
        p.drawRoundedRect(box, 8, 8)
        p.setPen(QColor("white"))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        if r >= 2:  # read-only stars for ratings 2 to 5
            stars = "★" * r
            sbox = QRectF(
                box.left(), box.bottom() + 6, p.fontMetrics().horizontalAdvance(stars) + 16, h * 0.8
            )
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawRoundedRect(sbox, 6, 6)
            p.setPen(QColor("#ffd54f"))
            p.drawText(sbox, Qt.AlignmentFlag.AlignCenter, stars)


class TagChips(QScrollArea):
    """Chips for the current photo's keywords, in one scrollable row. User tags can be removed;
    system tags and keywords from other apps are shown read-only (decision Q10).

    Uses a standard QHBoxLayout so Qt owns every layout item (no Python-held layout items)."""

    remove_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFixedHeight(44)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        inner = QWidget()
        self._row = QHBoxLayout(inner)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.addStretch(1)
        self.setWidget(inner)
        self._chips: list[QToolButton] = []

    def set_keywords(self, keywords: list[str], enabled: bool) -> None:
        for chip in self._chips:
            self._row.removeWidget(chip)
            chip.hide()
            chip.deleteLater()
        self._chips = []
        inner = self.widget()
        for k in keywords:
            chip = QToolButton(inner)
            chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            if tag_logic.is_user_tag(k):
                chip.setText(f"{k}  ✕")
                chip.setToolTip(f"Remove the tag {k}")
                chip.setAccessibleName(f"Tag {k}, press to remove")
                chip.setStyleSheet(
                    "QToolButton{background:#e3f2fd;color:#0d3c61;border:1px solid #64b5f6;"
                    "border-radius:10px;padding:3px 8px;}"
                )
                chip.setEnabled(enabled)
                chip.clicked.connect(_bind(self.remove_requested.emit, k))
            else:
                system = tag_logic.is_system(k)
                chip.setText(f"🔒 {k}")
                chip.setToolTip(
                    "Managed by the app" if system else "From another app; this app leaves it unchanged"
                )
                chip.setAccessibleName(f"Read-only tag {k}")
                chip.setStyleSheet(
                    "QToolButton{background:#eeeeee;color:#424242;border:1px dashed #9e9e9e;"
                    "border-radius:10px;padding:3px 8px;}"
                )
            self._row.insertWidget(self._row.count() - 1, chip)  # before the stretch
            self._chips.append(chip)


def _bind(fn: Callable[[str], None], value: str) -> Callable[[], None]:
    return lambda: fn(value)


# ---------------------------------------------------------------- icons


def _canvas() -> tuple[QPixmap, QPainter]:
    pm = QPixmap(96, 96)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    return pm, p


def _pen(color: QColor, width: float = 10) -> QPen:
    return QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)


def keep_icon() -> QIcon:
    pm, p = _canvas()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(GREEN)
    p.drawEllipse(4, 4, 88, 88)
    p.setPen(_pen(QColor("white"), 11))
    path = QPainterPath(QPointF(26, 50))
    path.lineTo(42, 66)
    path.lineTo(70, 32)
    p.drawPath(path)
    p.end()
    return QIcon(pm)


def archive_icon() -> QIcon:
    pm, p = _canvas()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(RED)
    p.drawEllipse(4, 4, 88, 88)
    p.setPen(_pen(QColor("white"), 7))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(26, 30, 44, 12)  # box lid
    p.drawRect(30, 42, 36, 28)  # box body
    p.drawLine(42, 52, 54, 52)
    p.end()
    return QIcon(pm)


def arrow_icon(left: bool) -> QIcon:
    pm, p = _canvas()
    p.setPen(_pen(SLATE, 12))
    path = QPainterPath()
    if left:
        path.moveTo(54, 20)
        path.lineTo(26, 48)
        path.lineTo(54, 76)
        p.drawLine(26, 48, 78, 48)
    else:
        path.moveTo(42, 20)
        path.lineTo(70, 48)
        path.lineTo(42, 76)
        p.drawLine(18, 48, 70, 48)
    p.drawPath(path)
    p.end()
    return QIcon(pm)


def undo_icon() -> QIcon:
    pm, p = _canvas()
    p.setPen(_pen(SLATE, 10))
    p.drawArc(QRectF(22, 26, 56, 50), 90 * 16, -250 * 16)
    path = QPainterPath(QPointF(36, 14))
    path.lineTo(50, 26)
    path.lineTo(36, 40)
    p.drawPath(path)
    p.end()
    return QIcon(pm)


def info_icon() -> QIcon:
    pm, p = _canvas()
    p.setPen(_pen(SLATE, 8))
    p.drawEllipse(10, 10, 76, 76)
    p.drawLine(48, 44, 48, 70)
    p.setBrush(SLATE)
    p.drawEllipse(QPointF(48, 30), 3, 3)
    p.end()
    return QIcon(pm)

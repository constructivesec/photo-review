"""Custom widgets: the fit-to-window photo view with NO badge, and painted button icons."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

RED = QColor("#d32f2f")
GREEN = QColor("#2e7d32")


class ImageView(QWidget):
    """Draws one image scaled to fit (aspect preserved), or a centered message."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(200, 150)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._image: QImage | None = None
        self._scaled: QPixmap | None = None
        self._message = ""
        self._no_badge = False

    def set_image(self, image: QImage | None, no_badge: bool = False) -> None:
        self._image, self._scaled, self._message = image, None, ""
        self._no_badge = no_badge
        self.update()

    def set_message(self, text: str, no_badge: bool = False) -> None:
        self._image, self._scaled, self._message = None, None, text
        self._no_badge = no_badge
        self.update()

    def resizeEvent(self, event) -> None:
        self._scaled = None
        super().resizeEvent(event)

    def _target_rect(self) -> QRectF:
        assert self._image is not None
        iw, ih = self._image.width(), self._image.height()
        scale = min(self.width() / iw, self.height() / ih)
        w, h = iw * scale, ih * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#202020"))
        badge_area = QRectF(self.rect())
        if self._image is not None and not self._image.isNull():
            rect = self._target_rect()
            dpr = self.devicePixelRatioF()
            if self._scaled is None:
                size = QSize(max(1, round(rect.width() * dpr)), max(1, round(rect.height() * dpr)))
                self._scaled = QPixmap.fromImage(self._image.scaled(
                    size, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
                self._scaled.setDevicePixelRatio(dpr)
            p.drawPixmap(rect.topLeft(), self._scaled)
            badge_area = rect
        elif self._message:
            p.setPen(QColor("#e0e0e0"))
            f = QFont(self.font())
            f.setPointSize(16)
            p.setFont(f)
            p.drawText(self.rect().adjusted(20, 20, -20, -20),
                       Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self._message)
        if self._no_badge:
            self._draw_badge(p, badge_area)
        p.end()

    def _draw_badge(self, p: QPainter, area: QRectF) -> None:
        h = max(40.0, min(110.0, min(self.width(), self.height()) * 0.14))
        w = h * 1.8
        r = QRectF(area.left() + 12, area.top() + 12, w, h)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor("white"), max(3.0, h * 0.06)))
        p.setBrush(RED)
        p.drawRoundedRect(r, h * 0.15, h * 0.15)
        f = QFont(self.font())
        f.setBold(True)
        f.setPixelSize(int(h * 0.62))
        p.setFont(f)
        p.setPen(QColor("white"))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, "NO")


# ---------------------------------------------------------------- icons

def _canvas(size: int = 96) -> tuple[QPixmap, QPainter]:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    return pm, p


def _circle_icon(color: QColor, draw) -> QIcon:
    pm, p = _canvas()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawEllipse(4, 4, 88, 88)
    pen = QPen(QColor("white"), 11, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
               Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    draw(p)
    p.end()
    return QIcon(pm)


def yes_icon() -> QIcon:
    def draw(p: QPainter):
        path = QPainterPath(QPointF(26, 50))
        path.lineTo(42, 66)
        path.lineTo(70, 32)
        p.drawPath(path)
    return _circle_icon(GREEN, draw)


def no_icon() -> QIcon:
    def draw(p: QPainter):
        p.drawLine(32, 32, 64, 64)
        p.drawLine(64, 32, 32, 64)
    return _circle_icon(RED, draw)


def arrow_icon(left: bool) -> QIcon:
    pm, p = _canvas()
    pen = QPen(QColor("#37474f"), 12, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
               Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    path = QPainterPath()
    if left:
        path.moveTo(56, 20); path.lineTo(28, 48); path.lineTo(56, 76)
        p.drawLine(28, 48, 78, 48)
    else:
        path.moveTo(40, 20); path.lineTo(68, 48); path.lineTo(40, 76)
        p.drawLine(18, 48, 68, 48)
    p.drawPath(path)
    p.end()
    return QIcon(pm)


def undo_icon() -> QIcon:
    pm, p = _canvas()
    pen = QPen(QColor("#37474f"), 10, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
               Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.drawArc(QRectF(22, 26, 56, 50), 90 * 16, -250 * 16)
    path = QPainterPath(QPointF(36, 14))
    path.lineTo(50, 26)
    path.lineTo(36, 40)
    p.drawPath(path)
    p.end()
    return QIcon(pm)

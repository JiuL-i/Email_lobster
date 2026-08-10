"""Generate the launcher icon programmatically using QPainter.

Produces a multi-size .ico file containing 16/24/32/48/64/128/256 px images.
The icon features a coral background and a stylised lobster drawn from
primitives so it renders identically on machines without colour-emoji fonts.

Usage::

    python gui/icon.py assets/lobster.ico
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QApplication


ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _draw_lobster(painter: QPainter, side: int) -> None:
    """Draw a stylised lobster centred inside an `side × side` square."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    # Background: rounded square with vertical coral gradient
    gradient = QLinearGradient(0, 0, 0, side)
    gradient.setColorAt(0.0, QColor("#ff7a59"))
    gradient.setColorAt(1.0, QColor("#c63b1f"))
    radius = max(2, side // 5)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, side, side), radius, radius)
    painter.fillPath(path, QBrush(gradient))

    # Inner highlight stroke
    pen = QPen(QColor(255, 255, 255, 60), max(1, side // 32))
    painter.setPen(pen)
    painter.drawRoundedRect(QRectF(1, 1, side - 2, side - 2), radius - 1, radius - 1)

    # Lobster body (ellipse)
    cx, cy = side / 2, side / 2
    body_w, body_h = side * 0.42, side * 0.36
    body_rect = QRectF(cx - body_w / 2, cy - body_h / 2, body_w, body_h)

    body_gradient = QLinearGradient(body_rect.left(), body_rect.top(), body_rect.right(), body_rect.bottom())
    body_gradient.setColorAt(0.0, QColor("#ffb199"))
    body_gradient.setColorAt(1.0, QColor("#a82a14"))
    painter.setPen(QPen(QColor("#5a150a"), max(1, side // 24)))
    painter.setBrush(QBrush(body_gradient))
    painter.drawEllipse(body_rect)

    # Body segmentation lines
    painter.setPen(QPen(QColor("#5a150a"), max(1, side // 32)))
    seg_count = 3
    for i in range(seg_count):
        x = body_rect.left() + (i + 1) * body_w / (seg_count + 1)
        painter.drawLine(QPointF(x, body_rect.top() + 2), QPointF(x, body_rect.bottom() - 2))

    # Head (smaller ellipse above body)
    head_w, head_h = side * 0.22, side * 0.18
    head_rect = QRectF(cx - head_w / 2, cy - body_h / 2 - head_h * 0.65, head_w, head_h)
    painter.setPen(QPen(QColor("#5a150a"), max(1, side // 24)))
    painter.setBrush(QBrush(body_gradient))
    painter.drawEllipse(head_rect)

    # Eyes
    eye_r = max(1.0, side * 0.025)
    eye_pen = QPen(QColor("white"), 0)
    painter.setPen(eye_pen)
    painter.setBrush(QBrush(QColor("white")))
    eye_y = head_rect.center().y()
    painter.drawEllipse(QPointF(cx - head_w * 0.25, eye_y), eye_r, eye_r)
    painter.drawEllipse(QPointF(cx + head_w * 0.25, eye_y), eye_r, eye_r)
    painter.setPen(eye_pen)
    painter.setBrush(QBrush(QColor("#5a150a")))
    painter.drawEllipse(QPointF(cx - head_w * 0.25, eye_y), eye_r * 0.5, eye_r * 0.5)
    painter.drawEllipse(QPointF(cx + head_w * 0.25, eye_y), eye_r * 0.5, eye_r * 0.5)
    painter.setBrush(QBrush(QColor("#5a150a")))
    painter.drawEllipse(QPointF(cx - head_w * 0.25, eye_y), eye_r * 0.5, eye_r * 0.5)
    painter.drawEllipse(QPointF(cx + head_w * 0.25, eye_y), eye_r * 0.5, eye_r * 0.5)

    # Antennae (curved lines from head)
    pen = QPen(QColor("#5a150a"), max(1, side // 28))
    painter.setPen(pen)
    start_y = head_rect.top()
    for sign, sweep in ((-1, -1), (1, 1)):
        ctrl1 = QPointF(cx + sign * head_w * 0.4, start_y - side * 0.16)
        end = QPointF(cx + sign * head_w * 0.9, start_y - side * 0.30)
        path = QPainterPath(QPointF(cx + sign * head_w * 0.15, start_y))
        path.cubicTo(ctrl1, ctrl1, end)
        painter.drawPath(path)

    # Two claws (rounded rects flanking the head)
    claw_w, claw_h = side * 0.16, side * 0.10
    claw_pen = QPen(QColor("#5a150a"), max(1, side // 24))
    painter.setPen(claw_pen)
    painter.setBrush(QBrush(QColor("#a82a14")))
    for sign in (-1, 1):
        rect = QRectF(
            cx + sign * (head_w / 2 + claw_w * 0.55) - claw_w / 2 * sign,
            head_rect.center().y() - claw_h / 2,
            claw_w,
            claw_h,
        )
        painter.drawEllipse(rect)

    # Legs (5 segments on each side)
    leg_pen = QPen(QColor("#5a150a"), max(1, side // 36))
    painter.setPen(leg_pen)
    for i in range(3):
        ratio = (i + 1) / 4.0
        y = body_rect.top() + body_h * ratio
        for sign in (-1, 1):
            x1 = cx + sign * (body_w / 2)
            x2 = cx + sign * (body_w / 2 + side * 0.18)
            painter.drawLine(QPointF(x1, y), QPointF(x2, y + sign * side * 0.04))


def render_pixmap(side: int) -> QPixmap:
    pixmap = QPixmap(side, side)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        _draw_lobster(painter, side)
    finally:
        painter.end()
    return pixmap


def render_images() -> list[QImage]:
    return [render_pixmap(size).toImage() for size in ICON_SIZES]


def save_ico(path: os.PathLike[str] | str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    images = render_images()
    if not images:
        raise RuntimeError("no icon sizes generated")
    if not images[0].save(str(target), "ICO"):
        raise RuntimeError(f"failed to write {target}")
    return target


def ensure_icon(path: os.PathLike[str] | str) -> Path:
    target = Path(path)
    if target.exists() and target.stat().st_size > 0:
        return target
    return save_ico(target)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: python gui/icon.py <output.ico>", file=sys.stderr)
        return 1
    _app = QApplication.instance() or QApplication([])
    target = save_ico(argv[0])
    print(f"wrote {target} ({target.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
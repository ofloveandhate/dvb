import os
os.environ["QT_QPA_PLATFORM"] = "linuxfb:fb=/dev/fb0"
os.environ["QT_QPA_GENERIC_PLUGINS"] = "evdevtouch:/dev/input/event6"

import sys
from PyQt5.QtWidgets import QApplication, QMainWindow, QWidget, QLabel
from PyQt5.QtCore import Qt, QPoint, QEvent, QObject
from PyQt5.QtGui import QPainter, QColor, QMouseEvent

SCREEN_W = 480
SCREEN_H = 320

# observed mins and maxes from calibration touches
RAW_X_MIN = 46
RAW_X_MAX = 434
RAW_Y_MIN = 22
RAW_Y_MAX = 287


def transform_coords(x, y, w, h):
    nx = (x - RAW_X_MIN) / (RAW_X_MAX - RAW_X_MIN)
    ny = (y - RAW_Y_MIN) / (RAW_Y_MAX - RAW_Y_MIN)
    screen_x = int((1.0 - ny) * w)
    screen_y = int(nx * h)
    return screen_x, screen_y


class TouchFilter(QObject):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.processing = False

    def eventFilter(self, obj, event):
        if self.processing:
            return False

        if event.type() in (
            QEvent.MouseButtonPress,
            QEvent.MouseButtonRelease,
            QEvent.MouseMove,
        ):
            raw_x = event.globalPos().x()
            raw_y = event.globalPos().y()
            new_x, new_y = transform_coords(raw_x, raw_y, SCREEN_W, SCREEN_H)

            print(f"raw=({raw_x},{raw_y}) -> fixed=({new_x},{new_y})")

            target = self.app.widgetAt(new_x, new_y)
            if target is None:
                return True

            local_pos = target.mapFromGlobal(QPoint(new_x, new_y))

            new_event = QMouseEvent(
                event.type(),
                local_pos,
                QPoint(new_x, new_y),
                event.button(),
                event.buttons(),
                event.modifiers(),
            )

            self.processing = True
            self.app.sendEvent(target, new_event)
            self.processing = False
            return True

        return False


class CrossHair(QWidget):
    """draws a crosshair at the last touch position"""

    def __init__(self):
        super().__init__()
        self.touch_x = None
        self.touch_y = None

    def set_touch(self, x, y):
        self.touch_x = x
        self.touch_y = y
        self.update()

    def paintEvent(self, event):
        if self.touch_x is None:
            return
        painter = QPainter(self)

        # crosshair lines
        painter.setPen(QColor(255, 0, 0))
        painter.drawLine(self.touch_x - 20, self.touch_y, self.touch_x + 20, self.touch_y)
        painter.drawLine(self.touch_x, self.touch_y - 20, self.touch_x, self.touch_y + 20)

        # coordinate label
        painter.setPen(QColor(255, 255, 0))
        painter.drawText(self.touch_x + 5, self.touch_y - 5, f"({self.touch_x},{self.touch_y})")


class CalibrationWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setGeometry(0, 0, SCREEN_W, SCREEN_H)

        # black background
        self.setStyleSheet("background-color: black;")

        # crosshair widget as central widget
        self.crosshair = CrossHair()
        self.crosshair.setStyleSheet("background-color: black;")
        self.setCentralWidget(self.crosshair)

        # corner labels
        self._add_corner_labels()

        # status label in center
        self.status = QLabel("touch the corners", self.crosshair)
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setGeometry(SCREEN_W // 2 - 100, SCREEN_H // 2 - 15, 200, 30)
        self.status.setStyleSheet("color: white; background: transparent; font-size: 14px;")

    def _add_corner_labels(self):
        offset = 10
        style = "color: lime; background: transparent; font-size: 14px; font-weight: bold;"

        tl = QLabel("TL", self.crosshair)
        tl.setStyleSheet(style)
        tl.move(offset, offset)

        tr = QLabel("TR", self.crosshair)
        tr.setStyleSheet(style)
        tr.move(SCREEN_W - offset - 25, offset)

        bl = QLabel("BL", self.crosshair)
        bl.setStyleSheet(style)
        bl.move(offset, SCREEN_H - offset - 25)

        br = QLabel("BR", self.crosshair)
        br.setStyleSheet(style)
        br.move(SCREEN_W - offset - 25, SCREEN_H - offset - 25)

    def mousePressEvent(self, event):
        x = event.x()
        y = event.y()
        self.crosshair.set_touch(x, y)
        self.status.setText(f"({x},{y})")
        print(f"touch: ({x},{y})")


if __name__ == "__main__":
    app = QApplication(sys.argv)

    touch_filter = TouchFilter(app)
    app.installEventFilter(touch_filter)

    window = CalibrationWindow()
    window.show()

    sys.exit(app.exec_())
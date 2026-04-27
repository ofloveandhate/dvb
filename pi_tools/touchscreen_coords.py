import sys
from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtCore import QObject, QEvent

class DebugFilter(QObject):
    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonPress:
            print(f"TOUCH: ({event.globalPos().x()}, {event.globalPos().y()})")
        return False

app = QApplication(sys.argv)
w = QWidget()
w.setGeometry(0, 0, 480, 320)
w.setWindowFlags(0x00000800)  # frameless
w.show()

f = DebugFilter()
app.installEventFilter(f)

sys.exit(app.exec_())
import sys
import os
from src.dpi import configure_dpi_awareness
from src.qt_coordinates import prepare_qt_pixel_coordinates

# Set physical-pixel awareness before Qt imports or any window creation.
DPI_RECEIPT = configure_dpi_awareness()
QT_PIXEL_RECEIPT = prepare_qt_pixel_coordinates()

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt
QApplication.setAttribute(Qt.AA_DisableHighDpiScaling, True)

sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from src.main import ControlPanel
from src.logger import setup_logging

if __name__ == "__main__":
    setup_logging()
    app = QApplication(sys.argv)
    panel = ControlPanel()
    panel.show()
    sys.exit(app.exec_())
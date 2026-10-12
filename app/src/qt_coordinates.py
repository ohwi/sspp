"""Set this application's Qt environment to native pixel coordinates before Qt.

Import is inert. Passing a mapping changes only that mapping; the default changes
only the current process's os.environ, never Windows/user persistent settings.
The caller must additionally set AA_DisableHighDpiScaling before QApplication.
"""
from collections.abc import MutableMapping
import os

VALUES = {'QT_ENABLE_HIGHDPI_SCALING': '0',
          'QT_AUTO_SCREEN_SCALE_FACTOR': '0',
          'QT_SCALE_FACTOR': '1'}
KEYS = tuple(VALUES) + ('QT_SCREEN_SCALE_FACTORS',)


def prepare_qt_pixel_coordinates(environ=None):
    current_process = environ is None
    environment = os.environ if current_process else environ
    if not isinstance(environment, MutableMapping):
        raise TypeError('environ must be a mutable mapping')
    before = {key: environment.get(key) for key in KEYS}
    environment.update(VALUES)
    environment.pop('QT_SCREEN_SCALE_FACTORS', None)
    return {'scope': 'current_process_environment' if current_process else 'supplied_mapping',
            'previous': before, 'effective': {key: environment.get(key) for key in KEYS},
            'required_qt_attribute': 'AA_DisableHighDpiScaling_before_QApplication',
            'persistent_or_parent_settings_changed': False}

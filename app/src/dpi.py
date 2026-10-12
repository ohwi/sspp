"""Select process DPI awareness before Qt, MSS, or any HWND is initialized.

Importing this module has no Windows API side effects. The application manifest
is preferred; an already configured process is never reconfigured. Tests inject
an inert backend and never alter the interactive desktop process.
"""
from __future__ import annotations
import ctypes
import sys
from ctypes import wintypes

ACCESS_DENIED = 5
UNSUPPORTED_CONTEXT_ERRORS = {87, 120, 127}
E_ACCESSDENIED = 0x80070005
E_INVALIDARG = 0x80070057


class _NativeDpiBackend:
    def __init__(self):
        self.user32 = ctypes.WinDLL('user32', use_last_error=True)
        try:
            self.shcore = ctypes.WinDLL('shcore', use_last_error=True)
        except OSError:
            self.shcore = None

    def set_context(self, context):
        function = getattr(self.user32, 'SetProcessDpiAwarenessContext', None)
        if function is None:
            raise NotImplementedError
        function.argtypes = [ctypes.c_void_p]
        function.restype = wintypes.BOOL
        ctypes.set_last_error(0)
        success = bool(function(ctypes.c_void_p(context)))
        return success, int(ctypes.get_last_error())

    def set_awareness(self, awareness):
        function = getattr(self.shcore, 'SetProcessDpiAwareness', None)
        if function is None:
            raise NotImplementedError
        function.argtypes = [ctypes.c_int]
        function.restype = ctypes.c_long
        return int(function(awareness)) & 0xffffffff

    def set_system(self):
        function = getattr(self.user32, 'SetProcessDPIAware', None)
        if function is None:
            raise NotImplementedError
        function.argtypes = []
        function.restype = wintypes.BOOL
        ctypes.set_last_error(0)
        success = bool(function())
        return success, int(ctypes.get_last_error())


def configure_dpi_awareness(backend=None):
    """Return a receipt without overwriting an existing process DPI mode.

    ``backend`` is injectable for mock-only tests and implements set_context,
    set_awareness and set_system. A configured result records the requested API
    mode, not proof of mixed-monitor capture correctness. Existing/manifest-set
    mode is intentionally reported as unknown rather than guessed.
    """
    attempts = []

    def finish(status, mode=None):
        return {'status': status, 'mode': mode, 'attempts': attempts}

    if backend is None:
        if sys.platform != 'win32':
            return finish('unsupported')
        try:
            backend = _NativeDpiBackend()
        except (OSError, AttributeError):
            return finish('unsupported')
    for mode, context in [('per_monitor_v2', -4), ('per_monitor_v1', -3)]:
        try:
            success, error = backend.set_context(context)
            success, error = bool(success), int(error)
        except (NotImplementedError, AttributeError):
            attempts.append({'api': 'SetProcessDpiAwarenessContext', 'mode': mode, 'available': False})
            continue
        except (OSError, TypeError, ValueError):
            attempts.append({'api': 'SetProcessDpiAwarenessContext', 'mode': mode, 'result': 'unexpected_error'})
            return finish('error')
        attempts.append({'api': 'SetProcessDpiAwarenessContext', 'mode': mode,
                         'success': success, 'error': error})
        if success:
            return finish('configured', mode)
        if error == ACCESS_DENIED:
            return finish('already_configured')
        if error not in UNSUPPORTED_CONTEXT_ERRORS:
            return finish('error')
    try:
        result = int(backend.set_awareness(2)) & 0xffffffff
    except (NotImplementedError, AttributeError):
        attempts.append({'api': 'SetProcessDpiAwareness', 'mode': 'per_monitor_v1', 'available': False})
    except (OSError, TypeError, ValueError):
        attempts.append({'api': 'SetProcessDpiAwareness', 'mode': 'per_monitor_v1', 'result': 'unexpected_error'})
        return finish('error')
    else:
        attempts.append({'api': 'SetProcessDpiAwareness', 'mode': 'per_monitor_v1', 'hresult': result})
        if result == 0:
            return finish('configured', 'per_monitor_v1')
        if result == E_ACCESSDENIED:
            return finish('already_configured')
        if result != E_INVALIDARG:
            return finish('error')
    try:
        success, error = backend.set_system()
        success, error = bool(success), int(error)
    except (NotImplementedError, AttributeError):
        attempts.append({'api': 'SetProcessDPIAware', 'mode': 'system', 'available': False})
        return finish('unsupported')
    except (OSError, TypeError, ValueError):
        attempts.append({'api': 'SetProcessDPIAware', 'mode': 'system', 'result': 'unexpected_error'})
        return finish('error')
    attempts.append({'api': 'SetProcessDPIAware', 'mode': 'system', 'success': success, 'error': error})
    if success:
        return finish('configured', 'system')
    return finish('already_configured' if error == ACCESS_DENIED else 'error')

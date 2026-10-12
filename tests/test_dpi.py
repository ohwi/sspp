"""DPI startup tests inject only mock APIs; no live DPI calls or HWNDs."""
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from src.dpi import configure_dpi_awareness


class FakeBackend:
    def __init__(self, contexts=None, awareness=None, system=None):
        self.contexts = contexts or {}
        self.awareness, self.system = awareness, system
        self.calls = []

    def set_context(self, value):
        self.calls.append(('context', value))
        if value not in self.contexts:
            raise NotImplementedError
        result = self.contexts[value]
        if isinstance(result, Exception):
            raise result
        return result

    def set_awareness(self, value):
        self.calls.append(('awareness', value))
        if self.awareness is None:
            raise NotImplementedError
        return self.awareness

    def set_system(self):
        self.calls.append(('system',))
        if self.system is None:
            raise NotImplementedError
        return self.system


class DpiTests(unittest.TestCase):
    def test_per_monitor_v2_is_first_and_stops_after_success(self):
        backend=FakeBackend({-4:(True,0)})
        result=configure_dpi_awareness(backend)
        self.assertEqual((result['status'],result['mode']),('configured','per_monitor_v2'))
        self.assertEqual(backend.calls,[('context',-4)])

    def test_unsupported_v2_falls_back_to_v1_context(self):
        backend=FakeBackend({-4:(False,87),-3:(True,0)})
        self.assertEqual(configure_dpi_awareness(backend)['mode'],'per_monitor_v1')
        self.assertEqual(backend.calls,[('context',-4),('context',-3)])

    def test_old_context_api_uses_shcore_v1(self):
        backend=FakeBackend(awareness=0)
        self.assertEqual(configure_dpi_awareness(backend)['mode'],'per_monitor_v1')
        self.assertEqual(backend.calls,[('context',-4),('context',-3),('awareness',2)])

    def test_system_fallback_when_per_monitor_apis_are_unavailable(self):
        backend=FakeBackend(system=(True,0))
        result=configure_dpi_awareness(backend)
        self.assertEqual((result['status'],result['mode']),('configured','system'))
        self.assertEqual(backend.calls[-1],('system',))

    def test_invalid_shcore_argument_can_fall_back_to_system(self):
        backend=FakeBackend(awareness=0x80070057,system=(True,0))
        self.assertEqual(configure_dpi_awareness(backend)['mode'],'system')

    def test_context_access_denied_never_overwrites_manifest_or_existing_mode(self):
        backend=FakeBackend({-4:(False,5),-3:(True,0)},awareness=0,system=(True,0))
        result=configure_dpi_awareness(backend)
        self.assertEqual(result['status'],'already_configured')
        self.assertIsNone(result['mode'])
        self.assertEqual(backend.calls,[('context',-4)])

    def test_shcore_access_denied_stops_without_system_fallback(self):
        for hresult in (0x80070005,-2147024891):
            backend=FakeBackend(awareness=hresult,system=(True,0))
            self.assertEqual(configure_dpi_awareness(backend)['status'],'already_configured')
            self.assertNotIn(('system',),backend.calls)

    def test_unexpected_context_failure_never_guesses_a_lower_mode(self):
        for error in (0,31):
            backend=FakeBackend({-4:(False,error),-3:(True,0)},awareness=0,system=(True,0))
            self.assertEqual(configure_dpi_awareness(backend)['status'],'error')
            self.assertEqual(backend.calls,[('context',-4)])

    def test_backend_exception_is_reported_and_does_not_continue(self):
        backend=FakeBackend({-4:OSError('mock failure')},awareness=0)
        self.assertEqual(configure_dpi_awareness(backend)['status'],'error')
        self.assertEqual(backend.calls,[('context',-4)])

    def test_unexpected_hresult_is_not_a_reason_to_downgrade(self):
        backend=FakeBackend(awareness=0x80004005,system=(True,0))
        self.assertEqual(configure_dpi_awareness(backend)['status'],'error')
        self.assertNotIn(('system',),backend.calls)

    def test_all_missing_apis_are_unsupported(self):
        self.assertEqual(configure_dpi_awareness(FakeBackend())['status'],'unsupported')

    def test_non_windows_default_does_not_create_native_backend(self):
        with patch('src.dpi.sys.platform','linux'),patch('src.dpi._NativeDpiBackend') as native:
            self.assertEqual(configure_dpi_awareness()['status'],'unsupported')
            native.assert_not_called()

    def test_application_manifest_requests_v2_with_legacy_per_monitor_fallback(self):
        path=Path(__file__).resolve().parents[1]/'app/dpi.manifest'
        root=ET.parse(path).getroot()
        namespaces={'v3':'urn:schemas-microsoft-com:asm.v3','old':'http://schemas.microsoft.com/SMI/2005/WindowsSettings','new':'http://schemas.microsoft.com/SMI/2016/WindowsSettings'}
        settings=root.find('v3:application/v3:windowsSettings',namespaces)
        self.assertEqual(settings.find('old:dpiAware',namespaces).text,'true/pm')
        self.assertEqual(settings.find('new:dpiAwareness',namespaces).text,'PerMonitorV2,PerMonitor')
        self.assertEqual(root.find('v3:trustInfo/v3:security/v3:requestedPrivileges/v3:requestedExecutionLevel',namespaces).attrib['level'],'asInvoker')


if __name__=='__main__':
    unittest.main()

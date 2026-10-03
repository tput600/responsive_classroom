"""Rendered Qt regressions for settings drafts and asynchronous command feedback.

No hardware or network I/O: the page uses the real Qt WebChannel service, with
one delayed bridge stub only for the in-flight editing race.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from classroom_app import ClassroomWindow, _javascript, _spin
from classroom_core import Settings, SettingsRepository
from PySide6.QtWidgets import QApplication


class SettingsWorkflowTests(unittest.TestCase):
    def test_drafts_validation_navigation_and_snapshot_stability(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            repository = SettingsRepository(Path(directory) / 'settings.json')
            repository.save(Settings(wled_devices=[]))
            window = ClassroomWindow(repository, start_io=False)
            window.show()
            js = lambda code: _javascript(app, window, code)
            try:
                _spin(app, lambda: bool(js('window.classroom?.ready')), 25)
                js("""window.classroom.navigate('timers');
                    const field=document.getElementById('question_seconds');
                    field.value='37';field.dispatchEvent(new Event('input',{bubbles:true}));""")
                self.assertTrue(js("!document.querySelector('[data-draft-status=timers-form]').hidden"))
                window.service.command('language', '{"language":"en_US"}')
                _spin(app, lambda: js('document.documentElement.lang') == 'en')
                self.assertEqual(js("document.getElementById('question_seconds').value"), '37')
                js("window.classroom.navigate('classroom');window.classroom.navigate('timers')")
                self.assertEqual(js("document.getElementById('question_seconds').value"), '37')
                js("document.querySelector('[data-draft-status=timers-form] button').click()")
                self.assertEqual(js("document.getElementById('question_seconds').value"), '10')
                self.assertTrue(js("document.querySelector('[data-draft-status=timers-form]').hidden"))

                # Overload is explicitly distinct from a recognition result and
                # clears as soon as fresh capture recovers.
                # Inspect transient renderer-only overload fixtures in the same
                # JavaScript turn, before a real periodic snapshot can replace them.
                overload_checks = json.loads(js("""JSON.stringify((()=>{
                    const original=structuredClone(window.classroom.state),status=structuredClone(original);
                    status.noise.capture_overloaded=true;renderState(status);
                    const shown=!document.getElementById('capture-warning').hidden;
                    const message=document.getElementById('command-result').textContent;
                    status.noise.capture_overloaded=false;renderState(status);
                    const cleared=document.getElementById('capture-warning').hidden;
                    renderState(original);return {shown,message,cleared};
                })())"""))
                self.assertTrue(overload_checks['shown'])
                self.assertIn('Voice commands pause', overload_checks['message'])
                self.assertTrue(overload_checks['cleared'])

                # Drive network fixtures through the real WebChannel. Renderer-only
                # fixtures can be overwritten by a queued periodic service snapshot.
                # Wait for the exact fixture and completion of each command before
                # advancing; never rely on renderer/host scheduling speed.
                def publish_networks(networks):
                    window.service._networks = networks
                    window.service._publish()
                    expected = json.dumps(networks, separators=(',', ':'))
                    _spin(app, lambda: js('JSON.stringify(window.classroom.state.networks)') == expected)

                _spin(app, lambda: js('pending.size') == 0)
                js("""window.originalNetworkBackend=backend;window.scans=[];
                    backend={command:(action,payload,done)=>{
                        if(action==='scan')window.scans.push(JSON.parse(payload));
                        done('{"ok":true}');
                    }};""")
                try:
                    # Zero networks: search cannot run; manual IP remains available.
                    publish_networks([])
                    self.assertTrue(js("document.getElementById('scan-button').disabled"))
                    self.assertFalse(js("document.getElementById('network-help').hidden"))
                    self.assertFalse(js("document.getElementById('connect-button').disabled"))
                    js("document.getElementById('scan-button').click()")
                    self.assertEqual(js('window.scans.length'), 0)

                    # One network: one click dispatches that exact subnet.
                    publish_networks([{'name':'Wi-Fi','cidr':'192.0.2.0/24'}])
                    js("document.getElementById('scan-button').click()")
                    _spin(app, lambda: js('pending.size') == 0)
                    self.assertEqual(js('window.scans.length'), 1)
                    self.assertEqual(js('window.scans[0].cidr'), '192.0.2.0/24')
                    self.assertTrue(js("document.getElementById('network-details').hidden"))

                    # Multiple networks: opening the chooser dispatches nothing.
                    publish_networks([{'name':'Wi-Fi','cidr':'192.0.2.0/24'},
                                      {'name':'Ethernet','cidr':'198.51.100.0/24'}])
                    js("document.getElementById('scan-button').click()")
                    self.assertEqual(js('window.scans.length'), 1)
                    self.assertFalse(js("document.getElementById('network-details').hidden"))
                    js("document.getElementById('network').value='198.51.100.0/24';document.getElementById('scan-network').click()")
                    _spin(app, lambda: js('pending.size') == 0)
                    self.assertEqual(js('window.scans.length'), 2)
                    self.assertEqual(js('window.scans[1].cidr'), '198.51.100.0/24')
                finally:
                    _spin(app, lambda: js('pending.size') == 0)
                    js('backend=window.originalNetworkBackend')
                    publish_networks([])

                # Invalid hidden controls are disclosed, focused, and never saved.
                js("""window.classroom.navigate('connection');
                    document.querySelector('#noise-form details').open=false;
                    document.getElementById('rising-seconds').value='bad';
                    document.getElementById('save-noise').click();""")
                self.assertTrue(js("document.querySelector('#noise-form details').open"))
                self.assertEqual(js('document.activeElement.id'), 'rising-seconds')
                self.assertEqual(js("document.getElementById('rising-seconds').getAttribute('aria-invalid')"), 'true')
                self.assertNotEqual(repository.load().noise_rising_enter_seconds, 'bad')

                # Settings shortcut opens nested disclosure and moves keyboard focus.
                js("document.querySelector('[data-section=voice-details]').click()")
                self.assertTrue(js("document.getElementById('voice-details').open"))
                self.assertTrue(js("document.activeElement===document.querySelector('#voice-details>summary')"))

                self.assertTrue(js("""(()=>{const e=document.getElementById('corrections'),ev=new WheelEvent('wheel',{deltaY:50,bubbles:true,cancelable:true});e.dispatchEvent(ev);return !ev.defaultPrevented;})()"""))
                # Discovery refresh retains the user's multi-panel selection.
                window.service._found_boards = [{'ip':'192.0.2.2','mac':'two','name':'Two'}]
                window.service._publish()
                _spin(app, lambda: js("document.querySelectorAll('#found-boards input').length") == 1)
                js("document.querySelector('#found-boards input').click()")
                window.service._found_boards.append({'ip':'192.0.2.3','mac':'three','name':'Three'})
                window.service._publish()
                _spin(app, lambda: js("document.querySelectorAll('#found-boards input').length") == 2)
                self.assertEqual(js("document.querySelector('#found-boards input:checked').value"), '192.0.2.2')

                # An unchanged snapshot must not reconstruct or rewrite panel rows.
                window.service.settings.wled_devices[:] = [{'mac':'test','ip':'192.0.2.1','name':'Test','enabled':True}]
                window.service._publish()
                _spin(app, lambda: js("document.querySelectorAll('#board-list tr').length") == 1)
                js("""window.rowMutations=0;window.rowObserver=new MutationObserver(v=>window.rowMutations+=v.length);
                    window.rowObserver.observe(document.getElementById('board-list'),{subtree:true,childList:true,attributes:true,characterData:true});""")
                for index in range(5):
                    # An unrelated transcript marker is a delivery barrier. It
                    # proves each snapshot reached the renderer before measuring.
                    marker = f'row-mutation-barrier-{index}'
                    window.service._transcript['status'] = marker
                    window.service._publish()
                    _spin(app, lambda: js('window.classroom.state.transcript.status') == marker)
                self.assertEqual(js('window.rowMutations'), 0)
                js('window.rowObserver.disconnect()')

                # A delayed save cannot erase edits made after submission. Duplicate
                # submissions are suppressed without blocking the navigation thread.
                js("""window.classroom.navigate('timers');window.realBackend=backend;window.saveCalls=0;
                    backend={command:(a,p,done)=>{window.saveCalls++;window.completeTimerSave=()=>done('{"ok":true}')}};
                    const input=document.getElementById('question_seconds');input.value='41';input.dispatchEvent(new Event('input',{bubbles:true}));
                    document.getElementById('timers-form').dispatchEvent(new Event('submit',{cancelable:true}));
                    document.getElementById('timers-form').dispatchEvent(new Event('submit',{cancelable:true}));
                    input.value='42';input.dispatchEvent(new Event('input',{bubbles:true}));""")
                self.assertTrue(js("document.getElementById('save-timers').disabled"))
                js('window.completeTimerSave()')
                _spin(app, lambda: not js("document.getElementById('save-timers').disabled"))
                self.assertEqual(js('window.saveCalls'), 1)
                self.assertEqual(js("document.getElementById('question_seconds').value"), '42')
                self.assertTrue(js("!document.querySelector('[data-draft-status=timers-form]').hidden"))
                js('backend=window.realBackend')
                js("document.getElementById('save-timers').click()")
                _spin(app, lambda: repository.load().question_seconds == 42 and js("!document.getElementById('save-timers').disabled && document.querySelector('[data-draft-status=timers-form]').hidden"))
                self.assertTrue(js("document.querySelector('[data-draft-status=timers-form]').hidden"))
                # A delayed orientation save must not delete a newer draft stored
                # for that board when the teacher moves to a different panel.
                window.service.settings.wled_devices[:] = [
                    {'mac':'A','ip':'192.0.2.10','name':'A','enabled':True},
                    {'mac':'B','ip':'192.0.2.11','name':'B','enabled':True},
                ]
                window.service._publish()
                _spin(app, lambda: js("document.querySelectorAll('#orientation-board option').length") == 2)
                js("""window.classroom.navigate('connection');
                    document.getElementById('orientation-details').open=true;
                    backend={command:(a,p,done)=>{window.completeOrientationSave=()=>done('{"ok":true}')}};
                    const board=document.getElementById('orientation-board'),rotation=document.getElementById('rotation');
                    board.focus();rotation.value='90';rotation.dispatchEvent(new Event('change',{bubbles:true}));
                    document.getElementById('orientation-form').dispatchEvent(new Event('submit',{cancelable:true}));
                    rotation.value='180';rotation.dispatchEvent(new Event('change',{bubbles:true}));
                    board.focus();board.value='B';board.dispatchEvent(new Event('change',{bubbles:true}));""")
                js('window.completeOrientationSave()')
                _spin(app, lambda: not js("document.querySelector('#orientation-form button[type=submit]').disabled"))
                js("(()=>{const board=document.getElementById('orientation-board');board.focus();board.value='A';board.dispatchEvent(new Event('change',{bubbles:true}));backend=window.realBackend})()")
                self.assertEqual(js("document.getElementById('rotation').value"), '180')
                self.assertTrue(js("!document.querySelector('[data-draft-status=orientation-form]').hidden"))
                self.assertFalse(window.page.errors, window.page.errors)
            finally:
                window.close()
                app.processEvents()

    def test_legacy_panel_metadata_does_not_crash_output_start(self):
        from classroom_service import ClassroomService
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            service = ClassroomService(Path(directory) / 'settings.json', Path('source/resources'), start_io=False)
            service.settings.wled_devices[:] = [
                {'ip':'192.0.2.1','mac':'minimal','future_metadata':'ignored'},
                {'ip':'invalid','mac':'damaged'},
            ]
            service.start_io = True
            service._output_enabled = True
            with patch('classroom_service.WledSession') as session, patch('classroom_service.LatestFrameWorker') as worker:
                service._start_output()
                panels = session.call_args.args[0]
                self.assertEqual(len(panels), 1)
                self.assertEqual(panels[0].name, 'WLED')
                self.assertEqual(panels[0].led_count, 64)
                worker.return_value.start.assert_called_once()
                self.assertIn('invalid', service._hardware_messages)
            service.worker = None
            service.start_io = False
            service.close()
            app.processEvents()

    def test_capture_diagnostics_are_optional_and_monotonic(self):
        from classroom_service import ClassroomService
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            service = ClassroomService(Path(directory) / 'settings.json', Path('source/resources'), start_io=False)
            self.assertIsNone(service.snapshot()['noise']['age_ms'])
            self.assertEqual(service.snapshot()['noise']['capture_dropped'], 0)
            self.assertFalse(service.snapshot()['noise']['capture_overloaded'])
            service._reading = SimpleNamespace(captured_at=12, speech_excluded=False, speech_guard_ready=True)
            with patch('classroom_service.time.monotonic', return_value=12.25):
                self.assertEqual(service.snapshot()['noise']['age_ms'], 250)
            with patch('classroom_service.time.monotonic', return_value=11):
                self.assertEqual(service.snapshot()['noise']['age_ms'], 0)
            service.close()
            app.processEvents()

import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from classroom_audio import CommandParser, NoiseAnalyzer
from classroom_core import BaseMode, ClassroomController, NoiseState, Settings, SettingsRepository
from classroom_hardware import LatestFrameWorker, WledDevice, WledSession


class RecoveryTests(unittest.TestCase):
    def test_microphone_recovery_resumes_notice_but_respects_manual_pause(self):
        controller = ClassroomController(Settings(), lambda _s, _callback: lambda: None)
        controller.set_base_mode(BaseMode.NOTICE)
        controller.set_microphone_status(False, 'removed')
        self.assertFalse(controller.state.noise_enabled)
        self.assertEqual(controller.state.noise_state, NoiseState.UNKNOWN)
        controller.set_microphone_status(True)
        self.assertTrue(controller.state.noise_enabled)
        controller.set_detection_enabled(False)
        controller.set_microphone_status(False)
        controller.set_microphone_status(True)
        self.assertFalse(controller.state.noise_enabled)

    def test_explicit_corrections_accept_complete_words_inside_conversation(self):
        parser = CommandParser(Settings(command_corrections={'glass': 'class', '題問': '提問'}))
        result = parser.parse('glass question', now=1)
        self.assertEqual(result.intent, 'QUESTION')
        self.assertEqual(result.text, 'glass question')
        self.assertEqual(result.corrected_text, 'class question')
        self.assertEqual(parser.parse('課堂 題 問', now=5).intent, 'QUESTION')
        self.assertEqual(parser.parse('the glass question was interesting', now=8).intent, 'QUESTION')
        self.assertEqual(parser.parse('class question please', now=10).intent, 'QUESTION')

    def test_corrections_round_trip_utf8(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SettingsRepository(Path(directory) / '設定.json')
            settings = Settings(command_corrections={'提文': '提問'})
            repository.save(settings)
            self.assertEqual(repository.load().command_corrections, {'提文': '提問'})

    def test_observed_prefix_error_is_corrected_only_before_an_exact_command(self):
        parser = CommandParser(Settings())
        result = parser.parse('特堂请注意老师。', now=1)
        self.assertEqual(result.text, '特堂请注意老师。')
        self.assertEqual(result.corrected_text, '課堂请注意老师')
        self.assertEqual(result.intent, 'NOTICE')
        self.assertEqual(parser.parse('特堂请注意老师今天的讲义', now=5).intent, 'NOTICE')

    def test_digital_silence_cannot_be_saved_as_a_quiet_baseline(self):
        analyzer = NoiseAnalyzer(Settings())
        analyzer.start_calibration(now=0, seconds=1)
        analyzer.feed(np.zeros(1600), now=.5)
        reading = analyzer.feed(np.zeros(1600), now=1)
        self.assertEqual(reading.calibration_result['quality'], 'REJECTED')
        self.assertIsNone(analyzer.baseline_dbfs)

    def test_slow_http_does_not_interrupt_udp_and_initial_failure_recovers(self):
        # Darwin binds only configured loopback addresses, unlike Linux's /8.
        device = WledDevice('127.0.0.1', 'test', 'test-mac', 'test', 64)
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(receiver.close)
        receiver.bind((device.ip, 4048))
        receiver.settimeout(3)
        started = time.monotonic()
        failed = [False]
        slow_requests = []
        calls = []

        def request(_ip, path, payload=None, **_kwargs):
            calls.append((path, payload))
            if path == '/json/info':
                if not failed[0]:
                    failed[0] = True
                    raise OSError('initial connection failed')
                if time.monotonic() - started > 2:
                    slow_requests.append(time.monotonic())
                    time.sleep(.6)
                return {'mac': device.mac, 'leds': {'count': 64}, 'live': True,
                        'lm': 'DDP', 'lip': '127.0.0.1'}
            return {'on': True, 'bri': 40} if payload is None else {'success': True}

        session = WledSession([device])
        worker = LatestFrameWorker(session, .03)
        frame = bytes((10, 20, 30)) * 64
        with patch('classroom_hardware._request_json', request):
            try:
                worker.submit(frame)
                worker.start()
                first, _ = receiver.recvfrom(2048)
                self.assertEqual(first[10:], frame)
                timestamps = []
                # Leave headroom for Darwin timer coalescing on shared runners;
                # the latency bound and delivery during blocked HTTP remain strict.
                end = time.monotonic() + 2.5
                while time.monotonic() < end:
                    data, _ = receiver.recvfrom(2048)
                    self.assertEqual(data[10:], frame)
                    timestamps.append(time.monotonic())
                self.assertTrue(any(sum(start <= stamp < start + .6 for stamp in timestamps) >= 3
                                    for start in slow_requests),
                                'UDP delivery must continue during a slow HTTP request')
                self.assertGreater(len(timestamps), 35)
                self.assertLess(max(b - a for a, b in zip(timestamps, timestamps[1:])), .25)
            finally:
                worker.stop(4)
                receiver.close()
        self.assertFalse(worker.is_running)
        self.assertTrue(any(payload and payload.get('live') is False for _, payload in calls))

    def test_recovery_follows_mac_preserves_direction_and_rejects_bad_frame(self):
        old = WledDevice('127.0.1.3', 'old', 'stable-mac', 'test', 64,
                         rotation=90, mirror_x=True, serpentine=True)
        new = WledDevice('127.0.1.4', 'new', 'stable-mac', 'new-test', 64)
        session = WledSession([old])
        session._saved[old.ip] = {'on': False, 'bri': 30}
        session._failures[old.ip] = 3
        session.health[old.ip] = 'offline'
        with patch('classroom_hardware.local_networks', return_value=[('LAN', old.ip, '127.0.1.0/24')]), \
             patch('classroom_hardware.discover_subnet', return_value=[new]):
            session._rediscover(threading.Event())
        self.assertEqual(session.devices[0].ip, new.ip)
        self.assertEqual(session.devices[0].rotation, 90)
        self.assertTrue(session.devices[0].mirror_x)
        self.assertTrue(session.devices[0].serpentine)
        self.assertEqual(session._saved[new.ip], {'on': False, 'bri': 30})
        with self.assertRaises(ValueError):
            session._check_identity(session.devices[0], {'mac': 'another-board', 'leds': {'count': 64}})
        worker = LatestFrameWorker(session)
        with self.assertRaises(ValueError):
            worker.submit(b'invalid')

    def test_beacon_uses_verified_drgb_pixels_and_accepts_its_own_receipt(self):
        device = WledDevice('127.0.0.1', 'Beacon', 'beacon-mac', '16.0.1', 64,
                            rotation=180)
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(receiver.close)
        receiver.bind((device.ip, 21324))
        receiver.settimeout(2)
        calls, statuses = [], []
        info = {'mac': device.mac, 'leds': {'count': 64}, 'brand': 'WLED-Beacon',
                'live': True, 'lm': 'UDP', 'lip': '127.0.0.1'}
        stop = threading.Event()

        def request(_ip, path, payload=None, **_kwargs):
            calls.append((path, payload))
            if path == '/json/info':
                if len(calls) > 3:
                    stop.set()
                return info
            return {'on': False, 'bri': 40} if payload is None else {'success': True}

        session = WledSession([device], lambda _ip, message: statuses.append(message))
        with patch('classroom_hardware._request_json', request):
            try:
                self.assertEqual(session.connect(), [])
                frame = bytes(range(192))
                session.send_frame(frame)
                packet, _ = receiver.recvfrom(2048)
                from classroom_hardware import orient_frame
                self.assertEqual(packet, bytes((2, 2)) + orient_frame(frame, rotation=180))
                self.assertFalse(session._other_source(device, info))
                self.assertTrue(session._other_source(device, {**info, 'lip': '192.0.2.10'}))
                session.monitor(stop)
                self.assertEqual(session.health[device.ip], 'receiving')
                self.assertIn('燈板已回報收到即時燈光', statuses)
                self.assertIn('已停止送燈，已恢復原設定', statuses)
            finally:
                session.close()
                receiver.close()

    def test_other_source_is_not_overwritten_on_connect_or_exit(self):
        device = WledDevice('127.0.1.5', 'test', 'stable-mac', 'test', 64)
        session = WledSession([device])
        calls = []

        def request(ip, path, payload=None, **_kwargs):
            calls.append((path, payload))
            return {'mac': device.mac, 'leds': {'count': 64}, 'live': True,
                    'lm': 'DDP', 'lip': '192.0.2.10'}

        with patch('classroom_hardware._request_json', request):
            errors = session.connect()
            session.close()
        self.assertEqual(len(errors), 1)
        self.assertEqual(session.health[device.ip], 'conflict')
        self.assertFalse(any(payload is not None for _, payload in calls))

    def test_restore_retries_one_timeout_then_restores_snapshot(self):
        device = WledDevice('127.0.1.6', 'test', 'stable-mac', 'test', 64)
        session = WledSession([device])
        session._saved[device.ip] = {'on': False, 'bri': 128, 'ps': -1, 'pl': -1, 'live': True, 'lor': 1}
        calls = []
        statuses = []

        def request(_ip, path, payload=None, **kwargs):
            calls.append((path, payload, kwargs.get('timeout')))
            if path == '/json/info':
                if sum(item[0] == path for item in calls) == 1:
                    raise TimeoutError('timed out')
                return {'mac': device.mac, 'leds': {'count': 64}, 'live': False}
            return {'success': True}

        session._set_status = lambda _ip, message: statuses.append(message)
        with patch('classroom_hardware._request_json', request):
            session.close()
        self.assertEqual(sum(path == '/json/info' for path, _, _ in calls), 2)
        self.assertEqual([payload for path, payload, _ in calls if path == '/json/state'],
                         [{'live': False}, {'on': False, 'bri': 128}])
        self.assertLessEqual(max(timeout for _, _, timeout in calls), 1.0)
        self.assertEqual(statuses[-1], '已停止送燈，已恢復原設定')

    def test_restore_permanent_timeout_is_bounded_to_three_attempts(self):
        device = WledDevice('127.0.1.7', 'test', 'stable-mac', 'test', 64)
        session = WledSession([device])
        session._saved[device.ip] = {'on': True, 'bri': 128}
        calls, statuses = [], []

        def request(_ip, path, payload=None, **kwargs):
            calls.append((path, payload, kwargs.get('timeout')))
            raise TimeoutError('timed out')

        session._set_status = lambda _ip, message: statuses.append(message)
        started = time.monotonic()
        with patch('classroom_hardware._request_json', request):
            session.close()
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(path == '/json/info' for path, _, _ in calls))
        self.assertTrue(all(0 < timeout <= 0.8 for _, _, timeout in calls))
        self.assertIn('timed out', statuses[-1])

    def test_restore_identity_and_source_conflicts_are_not_retried_or_overwritten(self):
        device = WledDevice('127.0.1.8', 'test', 'stable-mac', 'test', 64)
        for response, expected_error in (
            ({'mac': 'another-board', 'leds': {'count': 64}}, '另一塊燈板'),
            ({'mac': device.mac, 'leds': {'count': 64}, 'live': True,
              'lm': 'DDP', 'lip': '192.0.2.10'}, '另一來源'),
        ):
            with self.subTest(expected_error=expected_error):
                session = WledSession([device])
                session._saved[device.ip] = {'on': False, 'bri': 128}
                calls, statuses = [], []

                def request(_ip, path, payload=None, **_kwargs):
                    calls.append((path, payload))
                    return response

                session._set_status = lambda _ip, message: statuses.append(message)
                with patch('classroom_hardware._request_json', request):
                    session.close()
                self.assertEqual(len(calls), 1)
                self.assertFalse(any(payload is not None for _, payload in calls))
                self.assertIn(expected_error, statuses[-1])

    def test_restore_rejected_state_is_not_reported_successful(self):
        device = WledDevice('127.0.1.9', 'test', 'stable-mac', 'test', 64)
        session = WledSession([device])
        session._saved[device.ip] = {'on': True, 'bri': 128}
        calls, statuses = [], []

        def request(_ip, path, payload=None, **_kwargs):
            calls.append((path, payload))
            if path == '/json/info':
                return {'mac': device.mac, 'leds': {'count': 64}, 'live': False}
            return {'success': False}

        session._set_status = lambda _ip, message: statuses.append(message)
        with patch('classroom_hardware._request_json', request):
            session.close()
        self.assertEqual(len(calls), 2)
        self.assertIn('無法恢復', statuses[-1])
        self.assertNotIn('已恢復原設定', statuses[-1])


if __name__ == '__main__':
    unittest.main()

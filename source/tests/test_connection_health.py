import unittest
from unittest.mock import patch

import classroom_hardware as hardware
from classroom_hardware import WledDevice, WledSession


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now


class FakeStop:
    def __init__(self, clock, stop_at):
        self.clock = clock
        self.stop_at = stop_at
        self.stopped = False

    def is_set(self):
        return self.stopped

    def wait(self, seconds):
        self.clock.now += seconds
        self.stopped = self.clock.now >= self.stop_at
        return self.stopped


class FakeSocket:
    def __init__(self, failure=None):
        self.failure = failure
        self.packets = []
        self.closed = False

    def connect(self, _address):
        pass

    def getsockname(self):
        return ("192.0.2.10", 12345)

    def send(self, packet):
        if self.failure:
            raise self.failure
        self.packets.append(packet)

    def close(self):
        self.closed = True


class ConnectionHealthTests(unittest.TestCase):
    def device(self, ip="192.0.2.21", mac="board-mac"):
        return WledDevice(ip, "test", mac, "1", 64)

    def test_poll_failure_moves_receiving_to_checking_then_offline_and_recovers(self):
        device = self.device()
        states = []
        session = WledSession([device], lambda _ip, _message: states.append(session.health.get(device.ip)))
        clock = FakeClock()
        initial_socket = FakeSocket()
        reopened_socket = FakeSocket()
        session._sockets[device.ip] = initial_socket
        session._protocols[device.ip] = "DDP"
        session.health[device.ip] = "receiving"
        info_calls = 0

        def request(_ip, path, payload=None, **_kwargs):
            nonlocal info_calls
            if path == "/json/info":
                info_calls += 1
                if info_calls <= 3:
                    raise OSError("poll failed")
                return {"mac": device.mac, "leds": {"count": 64}, "live": True, "lm": "DDP"}
            return {"success": True} if payload is not None else {"on": True}

        def socket_factory(*_args, **_kwargs):
            return reopened_socket

        with (patch.object(hardware.time, "monotonic", clock.monotonic),
              patch.object(hardware, "_request_json", request),
              patch.object(hardware.socket, "socket", socket_factory),
              patch.object(hardware, "local_networks", lambda: []),
              patch.object(session, "close", lambda: None)):
            session.monitor(FakeStop(clock, 13.0))

        self.assertIn("checking", states)
        self.assertIn("offline", states)
        self.assertIn("sending", states)
        self.assertEqual(session.health[device.ip], "receiving")
        self.assertEqual(session._failures[device.ip], 0)
        self.assertTrue(initial_socket.closed)

    def test_successful_health_polls_are_about_one_second_apart(self):
        device = self.device()
        session = WledSession([device])
        sock = FakeSocket()
        session._sockets[device.ip] = sock
        session._protocols[device.ip] = "DDP"
        clock = FakeClock()
        poll_times = []

        def request(_ip, _path, **_kwargs):
            poll_times.append(clock.now)
            return {"mac": device.mac, "leds": {"count": 64}, "live": True, "lm": "DDP"}

        with (patch.object(hardware.time, "monotonic", clock.monotonic),
              patch.object(hardware, "_request_json", request),
              patch.object(session, "close", lambda: None)):
            session.monitor(FakeStop(clock, 1.1))

        self.assertGreaterEqual(len(poll_times), 2)
        self.assertAlmostEqual(poll_times[1] - poll_times[0], 1.0)

    def test_initial_open_failure_is_offline_and_sender_error_only_checks_that_board(self):
        first, second = self.device("192.0.2.21", "first"), self.device("192.0.2.22", "second")
        statuses = []
        session = WledSession([first, second], lambda ip, message: statuses.append((ip, message)))

        def fail_open(*_args, **_kwargs):
            raise OSError("unreachable")

        with patch.object(hardware, "_request_json", fail_open), patch.object(
                hardware.socket, "socket", lambda *_args, **_kwargs: FakeSocket()):
            self.assertEqual(len(session.connect()), 2)
        self.assertEqual(session.health[first.ip], "offline")

        broken, healthy = FakeSocket(OSError("send failed")), FakeSocket()
        session._sockets = {first.ip: broken, second.ip: healthy}
        session._protocols = {first.ip: "DDP", second.ip: "DDP"}
        session.health = {first.ip: "receiving", second.ip: "sending"}
        session._next_probe = {first.ip: 50.0}
        statuses.clear()
        frame = bytes((4, 5, 6)) * 64
        session.send_frame(frame)
        session.send_frame(frame)

        self.assertEqual(session.health[first.ip], "checking")
        self.assertEqual(session._next_probe[first.ip], 0)
        self.assertEqual(session.health[second.ip], "sending")
        self.assertEqual(healthy.packets[0][10:], frame)
        self.assertEqual(healthy.packets[1][10:], frame)
        self.assertEqual([ip for ip, _message in statuses], [first.ip])


if __name__ == "__main__":
    unittest.main()

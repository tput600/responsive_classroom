"""WLED discovery, realtime sessions, and 8x8 orientation."""

from __future__ import annotations

import concurrent.futures
import copy
import ipaddress
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, replace
from typing import Callable, Iterable


@dataclass(frozen=True)
class WledDevice:
    ip: str
    name: str
    mac: str
    version: str
    led_count: int
    rotation: int = 0
    mirror_x: bool = False
    mirror_y: bool = False
    serpentine: bool = False

    def __post_init__(self) -> None:
        ipaddress.ip_address(self.ip)
        if self.rotation not in (0, 90, 180, 270):
            raise ValueError("rotation must be 0, 90, 180, or 270")
        if not 1 <= self.led_count <= 4096:
            raise ValueError("LED count must be between 1 and 4096")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _request_json(ip: str, path: str, payload: dict | None = None,
                  timeout: float = 1.2) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"http://{ip}{path}", data=body,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method="GET" if body is None else "POST",
    )
    # Local boards must not be routed through Windows/browser proxy settings.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("WLED returned an unexpected JSON response")
    return data


def inspect_device(ip: str, timeout: float = 1.5) -> WledDevice:
    if ipaddress.ip_address(ip).version != 4:
        raise ValueError("WLED output requires an IPv4 address")
    info = _request_json(ip, "/json/info", timeout=timeout)
    leds = info.get("leds") or {}
    if not isinstance(leds, dict):
        leds = {}
    mac = str(info.get("mac", "")).strip().lower()
    if not mac:
        raise ValueError("Device answered /json/info but did not report a MAC address")
    return WledDevice(
        ip=str(ip), name=str(info.get("name") or info.get("brand") or "WLED"),
        mac=mac, version=str(info.get("ver", "unknown")),
        led_count=int(leds.get("count", 0)),
    )


def local_ipv4_addresses() -> list[str]:
    return [address for _name, address, _cidr in local_networks()]


def local_networks() -> list[tuple[str, str, str]]:
    """Use the actual adapter prefix, with physical LAN adapters before VPNs."""
    from PySide6.QtNetwork import QNetworkInterface

    found = []
    flags = QNetworkInterface.InterfaceFlag
    for interface in QNetworkInterface.allInterfaces():
        if not interface.flags() & flags.IsUp or interface.flags() & flags.IsLoopBack:
            continue
        for entry in interface.addressEntries():
            address = entry.ip().toString()
            try:
                ip = ipaddress.IPv4Address(address)
            except ValueError:
                continue
            if ip.is_link_local or ip.is_loopback:
                continue
            network = ipaddress.ip_network(f"{address}/{entry.prefixLength()}", strict=False)
            # A large VPN is never a suitable default for a local board scan.
            if network.num_addresses > 1024:
                continue
            found.append((interface.humanReadableName(), address, str(network)))
    return sorted(found, key=lambda item: ("vpn" in item[0].casefold(), item[0], item[1]))


def discover_subnet(cidr: str, timeout: float = 0.8,
                    progress: Callable[[int, int], None] | None = None,
                    stop: threading.Event | None = None) -> list[WledDevice]:
    network = ipaddress.ip_network(cidr, strict=False)
    hosts = list(network.hosts())
    if len(hosts) > 1024:
        raise ValueError("Choose a subnet with at most 1024 host addresses")
    found: dict[str, WledDevice] = {}
    completed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
        futures = {pool.submit(inspect_device, str(ip), timeout): str(ip) for ip in hosts}
        for future in concurrent.futures.as_completed(futures):
            if stop is not None and stop.is_set():
                for pending in futures:
                    pending.cancel()
                break
            completed += 1
            try:
                device = future.result()
                found[device.mac] = device
            except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
                pass
            if progress is not None:
                progress(completed, len(hosts))
    return list(found.values())


def orient_frame(frame: bytes, rotation: int = 0, mirror_x: bool = False,
                 mirror_y: bool = False, serpentine: bool = False) -> bytes:
    if len(frame) != 8 * 8 * 3:
        raise ValueError("8x8 RGB frame must contain exactly 192 bytes")
    if rotation not in (0, 90, 180, 270):
        raise ValueError("rotation must be 0, 90, 180, or 270")
    output = bytearray(len(frame))
    for y in range(8):
        for x in range(8):
            sx = 7 - x if mirror_x else x
            sy = 7 - y if mirror_y else y
            if rotation == 90:
                sx, sy = 7 - sy, sx
            elif rotation == 180:
                sx, sy = 7 - sx, 7 - sy
            elif rotation == 270:
                sx, sy = sy, 7 - sx
            physical_x = 7 - sx if serpentine and sy % 2 else sx
            source = (y * 8 + x) * 3
            target = (sy * 8 + physical_x) * 3
            output[target:target + 3] = frame[source:source + 3]
    return bytes(output)


def make_ddp_packet(frame: bytes, sequence: int = 1, push: bool = True) -> bytes:
    if not frame or len(frame) > 0xFFFF or len(frame) % 3:
        raise ValueError("DDP payload must be 1..65535 RGB bytes")
    if len(frame) > 1440:
        raise ValueError("DDP payload exceeds the safe Ethernet datagram size")
    flags = 0x40 | (0x01 if push else 0)
    # DDP v1, RGB 8-bit (0x0B), display destination (1), byte offset 0.
    return bytes((flags, sequence & 0x0F, 0x0B, 0x01, 0, 0, 0, 0,
                  (len(frame) >> 8) & 0xFF, len(frame) & 0xFF)) + frame


class WledSession:
    """Own one realtime session; save and restore each WLED state on shutdown."""

    def __init__(self, devices: Iterable[WledDevice],
                 status: Callable[[str, str], None] | None = None) -> None:
        self.devices = list(devices)
        if not 1 <= len(self.devices) <= 8:
            raise ValueError("Select between one and eight WLED boards")
        self.status = status or (lambda _ip, _message: None)
        self._last_status: dict[str, str] = {}
        self._saved: dict[str, dict] = {}
        self._sockets: dict[str, socket.socket] = {}
        self._protocols: dict[str, str] = {}
        self._sequence = 0
        self._lock = threading.Lock()
        self._sender_ips: dict[str, str] = {}
        self._failures: dict[str, int] = {}
        self._next_probe: dict[str, float] = {}
        self._last_discovery = 0.0
        self.health: dict[str, str] = {}

    def _set_status(self, ip: str, message: str) -> None:
        if self._last_status.get(ip) != message:
            self._last_status[ip] = message
            self.status(ip, message)

    def _check_identity(self, device: WledDevice, info: dict) -> None:
        if str(info.get("mac", "")).lower() != device.mac.lower():
            raise ValueError("IP 對應到另一塊燈板；將重新搜尋原裝置")
        if int(info.get("leds", {}).get("count", 0)) < 64:
            raise ValueError("燈板不足 64 顆 LED，請在 WLED 設為 8×8")

    def _other_source(self, device: WledDevice, info: dict) -> bool:
        if not info.get("live"):
            return False
        sender = str(info.get("lip") or "")
        ours = self._sender_ips.get(device.ip)
        return bool(sender and sender != ours) or str(info.get("lm", "")).upper() not in (
            "", self._protocols.get(device.ip, "DDP"), "HTTP")

    def _open(self, device: WledDevice) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((device.ip, 4048))
            self._sender_ips[device.ip] = sock.getsockname()[0]
            info = _request_json(device.ip, "/json/info")
            self._check_identity(device, info)
            # WLED-Beacon's DDP receipt does not update pixels on the tested board.
            # Use the port reported by that firmware, with its verified default
            # for older builds which omit udpport from /json/info.
            protocol = "UDP" if info.get("brand") == "WLED-Beacon" else "DDP"
            if protocol == "UDP":
                port = int(info.get("udpport", 21324))
                if not 1 <= port <= 65535:
                    raise ValueError("WLED 即時像素端口未啟用，請在 WLED 開啟 UDP 接收")
                sock.connect((device.ip, port))
            self._protocols[device.ip] = protocol
            if self._other_source(device, info):
                self.health[device.ip] = "conflict"
                raise RuntimeError("另一個程式正在送燈，請先停止該來源")
            if device.ip not in self._saved:
                self._saved[device.ip] = _request_json(device.ip, "/json/state")
            # Frames already contain the brightness cap. Do not dim them a second time.
            result = _request_json(device.ip, "/json/state", {"on": True, "bri": 255, "lor": 0})
            if result.get("success") is False:
                raise RuntimeError("WLED 拒絕啟用輸出")
            with self._lock:
                old = self._sockets.pop(device.ip, None)
                self._sockets[device.ip] = sock
            if old:
                old.close()
            self.health[device.ip] = "sending"
            self._failures[device.ip] = 0
            self._set_status(device.ip, "已連線，正在送出燈光；等待燈板回報")
        except Exception:
            sock.close()
            raise

    def connect(self) -> list[str]:
        errors = []
        for device in self.devices:
            try:
                self._open(device)
            except Exception as exc:
                errors.append(f"{device.ip}: {exc}")
                if self.health.get(device.ip) != "conflict":
                    self.health[device.ip] = "offline"
                self._set_status(device.ip, f"連線未成功，會自動重試：{exc}")
        return errors

    def monitor(self, stop: threading.Event) -> None:
        """HTTP and recovery never run on the frame-sending thread."""
        try:
            while not stop.is_set():
                now = time.monotonic()
                for device in list(self.devices):
                    if stop.is_set() or now < self._next_probe.get(device.ip, 0):
                        continue
                    connected = False
                    try:
                        with self._lock:
                            connected = device.ip in self._sockets
                        if not connected:
                            self._open(device)
                            self._next_probe[device.ip] = time.monotonic() + 1
                            continue
                        info = _request_json(device.ip, "/json/info", timeout=1)
                        self._check_identity(device, info)
                        if self._other_source(device, info):
                            self.health[device.ip] = "conflict"
                            raise RuntimeError("偵測到另一個送燈來源，已暫停輸出")
                        if info.get("live") and str(info.get("lm", "")).upper() == self._protocols.get(device.ip, "DDP"):
                            self.health[device.ip] = "receiving"
                            self._set_status(device.ip, "燈板已回報收到即時燈光")
                        elif info.get("live"):
                            self.health[device.ip] = "sending"
                            self._set_status(device.ip, "正在送出；尚未確認即時資料接收")
                        else:
                            # A reboot or disabled realtime override requires re-arming.
                            self._open(device)
                        self._failures[device.ip] = 0
                        self._next_probe[device.ip] = time.monotonic() + 1
                    except Exception as exc:
                        failures = self._failures.get(device.ip, 0) + 1
                        self._failures[device.ip] = failures
                        if failures >= 3 or isinstance(exc, (ValueError, RuntimeError)):
                            with self._lock:
                                sock = self._sockets.pop(device.ip, None)
                            if sock:
                                sock.close()
                        delay = min(15, 1.5 * 2 ** min(failures - 1, 4))
                        self._next_probe[device.ip] = time.monotonic() + delay
                        if self.health.get(device.ip) != "conflict":
                            # A failed poll means receipt is unconfirmed immediately.
                            # When opening a connection, remain offline until it succeeds.
                            self.health[device.ip] = (
                                "offline" if failures >= 3 or not connected else "checking"
                            )
                        self._set_status(device.ip, f"燈板回報暫時中斷，{delay:g} 秒後重試：{exc}")
                if now - self._last_discovery >= 60 and any(n >= 3 for n in self._failures.values()):
                    self._last_discovery = now
                    self._rediscover(stop)
                stop.wait(0.25)
        finally:
            self.close()

    def _rediscover(self, stop: threading.Event) -> None:
        missing = {d.mac.lower(): d for d in self.devices
                   if self._failures.get(d.ip, 0) >= 3 and self.health.get(d.ip) != "conflict"}
        for _name, _address, cidr in local_networks():
            if not missing or stop.is_set():
                break
            for found in discover_subnet(cidr, stop=stop):
                old = missing.pop(found.mac.lower(), None)
                if old is None or old.ip == found.ip:
                    continue
                updated = replace(old, ip=found.ip, name=found.name, version=found.version)
                self.devices[self.devices.index(old)] = updated
                if old.ip in self._saved:
                    self._saved[found.ip] = self._saved.pop(old.ip)
                self._set_status(old.ip, f"已重新找到燈板：{found.ip}")

    def send_frame(self, frame: bytes) -> None:
        if len(frame) != 192:
            raise ValueError("WLED 8x8 output requires one 192-byte RGB frame")
        self._sequence = self._sequence % 15 + 1
        with self._lock:
            targets = [(device, self._sockets.get(device.ip)) for device in self.devices]
        for device, sock in targets:
            if sock is None:
                continue
            oriented = orient_frame(frame, device.rotation, device.mirror_x,
                                    device.mirror_y, device.serpentine)
            packet = (bytes((2, 2)) + oriented if self._protocols.get(device.ip) == "UDP"
                      else make_ddp_packet(oriented, self._sequence))
            try:
                sock.send(packet)
            except OSError:
                self.health[device.ip] = "checking"
                self._next_probe[device.ip] = 0
                self._set_status(device.ip, "送燈暫時失敗，正在確認連線狀態")
                continue

    def close(self) -> None:
        with self._lock:
            sockets, self._sockets = self._sockets, {}
        for sock in sockets.values():
            sock.close()

        def restore(device: WledDevice) -> None:
            saved = self._saved.get(device.ip)
            if saved is None:
                return
            deadline = time.monotonic() + 5.0
            last_error = None
            for attempt in range(3):
                def request(path: str, payload: dict | None = None, timeout: float = 1.0) -> dict:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("restore deadline exceeded")
                    return _request_json(device.ip, path, payload, timeout=min(timeout, remaining))

                try:
                    info = request("/json/info", timeout=0.8)
                    self._check_identity(device, info)
                    if self._other_source(device, info):
                        self._set_status(device.ip, "已停止本程式輸出；燈板由另一來源控制")
                        return

                    def apply(payload: dict) -> None:
                        result = request("/json/state", payload)
                        if result.get("success") is False:
                            raise RuntimeError("WLED 拒絕恢復原設定")

                    apply({"live": False})
                    restored = copy.deepcopy(saved)
                    preset, playlist = restored.pop("ps", -1), restored.pop("pl", -1)
                    for volatile in ("live", "lor"):
                        restored.pop(volatile, None)
                    apply(restored)
                    if isinstance(preset, int) and preset >= 0:
                        apply({"ps": preset})
                    elif isinstance(playlist, int) and playlist >= 0:
                        apply({"pl": playlist})
                    self._set_status(device.ip, "已停止送燈，已恢復原設定")
                    return
                except (ValueError, RuntimeError, urllib.error.HTTPError) as exc:
                    self._set_status(device.ip, f"已停止送燈；目前無法恢復原設定：{exc}")
                    return
                except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError) as exc:
                    last_error = exc
                    if attempt == 2:
                        break
                    delay = min(0.1 * (attempt + 1), max(0.0, deadline - time.monotonic()))
                    if deadline - time.monotonic() <= delay:
                        break
                    time.sleep(delay)
                except Exception as exc:
                    self._set_status(device.ip, f"已停止送燈；目前無法恢復原設定：{exc}")
                    return
            self._set_status(device.ip, f"已停止送燈；目前無法恢復原設定：{last_error or 'restore deadline exceeded'}")
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(restore, self.devices))


class LatestFrameWorker:
    """Send the newest frame at the configured frame rate without queue growth."""

    def __init__(self, session: WledSession, interval: float = 0.05) -> None:
        self.session = session
        self.interval = interval
        self._latest: bytes | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._monitor: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        def run() -> None:
            self._monitor = threading.Thread(target=self.session.monitor, args=(self._stop,),
                                             name="WLED-connection", daemon=True)
            self._monitor.start()
            next_send = time.monotonic()
            while not self._stop.wait(max(0.0, next_send - time.monotonic())):
                with self._lock:
                    frame = self._latest
                if frame is not None:
                    self.session.send_frame(frame)
                next_send += self.interval
                if next_send < time.monotonic():
                    next_send = time.monotonic() + self.interval
            self._monitor.join()

        self._thread = threading.Thread(target=run, name="WLED-pixels", daemon=True)
        self._stop.clear()
        self._thread.start()

    def submit(self, frame: bytes) -> None:
        if len(frame) != 192:
            raise ValueError("WLED 8x8 output requires one 192-byte RGB frame")
        with self._lock:
            self._latest = bytes(frame)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

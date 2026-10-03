"""Small, asynchronous CSV session log; audio samples are never accepted."""
import csv
import queue
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path


class SessionLogger:
    columns = ('timestamp', 'mode', 'mode_source', 'raw_rms', 'dbfs',
               'smoothed_dbfs', 'noise_state', 'voice_command', 'pattern')

    def __init__(self, directory, enabled=True):
        self.error = ''
        self._queue = queue.Queue(maxsize=256)
        self._lock = threading.Lock()
        self._last = None
        self._last_write = 0.0
        self._closed = False
        self._thread = None
        self.path = None
        if not enabled:
            return
        try:
            directory = Path(directory)
            directory.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().astimezone()
            while True:
                self.path = directory / f"session-{stamp.strftime('%Y%m%d-%H%M%S-%f')}.csv"
                try:
                    with self.path.open('x', encoding='utf-8-sig', newline='') as f:
                        csv.writer(f).writerow(self.columns)
                    break
                except FileExistsError:
                    stamp += timedelta(microseconds=1)
            self._thread = threading.Thread(target=self._run, name='session-csv', daemon=True)
            self._thread.start()
        except Exception as exc:
            self.error = str(exc)
            self.path = None

    def write(self, *, mode, mode_source, raw_rms, dbfs, smoothed_dbfs,
              noise_state, voice_command, pattern):
        if self._thread is None:
            return
        try:
            now = datetime.now().astimezone()
            row = (now.isoformat(timespec='milliseconds'), mode, mode_source,
                   raw_rms, dbfs, smoothed_dbfs, noise_state,
                   voice_command or '', pattern)
            tick = time.monotonic()
            with self._lock:
                if self._closed:
                    return
                changed = self._last is None or any(
                    row[i] != self._last[i] for i in (1, 6, 8)
                ) or bool(row[7])
                if not changed and (tick - self._last_write) < 1:
                    return
                self._queue.put_nowait(row)
                self._last, self._last_write = row, tick
        except Exception as exc:
            self.error = str(exc)

    def _run(self):
        try:
            with self.path.open('a', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                while True:
                    row = self._queue.get()
                    try:
                        if row is None:
                            f.flush()
                            return
                        writer.writerow(row)
                    finally:
                        self._queue.task_done()
        except Exception as exc:
            self.error = str(exc)

    def close(self, timeout=2):
        if self._thread is None:
            return
        with self._lock:
            if self._closed:
                return
            self._closed = True
        deadline = time.monotonic() + max(0, timeout)
        try:
            self._queue.put(None, timeout=max(0, deadline - time.monotonic()))
            self._thread.join(max(0, deadline - time.monotonic()))
            if self._thread.is_alive():
                self.error = self.error or 'CSV writer did not finish before timeout'
        except Exception as exc:
            self.error = str(exc)

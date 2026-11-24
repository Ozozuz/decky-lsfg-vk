import socket
import struct
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py_modules"))

from lsfg_vk.gamescope_metrics import _read_compositor


def event(object_id, opcode, body):
    return struct.pack("=II", object_id, ((8 + len(body)) << 16) | opcode) + body


class GamescopeMetricsTests(unittest.TestCase):
    def test_queries_only_requested_app_and_handles_fragmented_events(self):
        app_id = 0x80000001
        interface = b"gamescope_control\0"

        def serve(listener):
            with listener.accept()[0] as connection, connection.makefile("rb") as stream:
                connection.settimeout(2)
                self.assertEqual(stream.read(24), event(1, 1, struct.pack("=I", 2)) + event(1, 0, struct.pack("=I", 3)))
                registry = struct.pack("=II", 8, len(interface)) + interface.ljust(20, b"\0") + struct.pack("=I", 7)
                global_event = event(2, 0, registry)
                connection.sendall(global_event[:5])
                connection.sendall(global_event[5:] + event(3, 0, struct.pack("=I", 0)))
                binding = struct.pack("=II", 8, len(interface)) + interface.ljust(20, b"\0") + struct.pack("=II", 6, 4)
                self.assertEqual(stream.read(44), event(2, 0, binding))
                self.assertEqual(stream.read(12), event(4, 6, struct.pack("=I", app_id)))
                connection.sendall(
                    event(4, 3, struct.pack("=III", 41, 10_000_000, 0))
                    + event(4, 3, struct.pack("=III", app_id, 16_500_000, 0))
                )

        with tempfile.TemporaryDirectory() as tmp, socket.socket(socket.AF_UNIX) as listener:
            path = Path(tmp) / "gamescope-0"
            listener.bind(str(path))
            listener.listen()
            with ThreadPoolExecutor(max_workers=1) as executor:
                server = executor.submit(serve, listener)
                self.assertEqual(_read_compositor(path, app_id, time.monotonic() + 2), 16.5)
                server.result()

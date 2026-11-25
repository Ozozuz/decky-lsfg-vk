import socket
import struct
import sys
import tempfile
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py_modules"))
sys.modules.setdefault("decky", types.SimpleNamespace(logger=Mock()))

from lsfg_vk import gamescope_metrics
from lsfg_vk.gamescope_metrics import _read_compositor


def event(object_id, opcode, body):
    return struct.pack("=II", object_id, ((8 + len(body)) << 16) | opcode) + body


class GamescopeMetricsTests(unittest.TestCase):
    def test_averages_consecutive_frames_for_requested_app(self):
        # Individual intervals give roughly 15–40 FPS. Over the batch the cadence is 30 FPS.
        intervals = [25_000_000] * 4 + [66_666_667, 33_333_333]
        self.assertAlmostEqual(1000 / self._sample(intervals * 2), 30)

    def test_uses_partial_sample_when_no_more_frames_arrive(self):
        self.assertEqual(self._sample([125_000_000], wait_for_more=True), 125)

    def test_reads_steam_app_ids_and_normalizes_signed_shortcut_ids(self):
        home = Path("/home/deck")
        with patch.object(gamescope_metrics, "read_frametime", return_value=16.7) as sample:
            self.assertEqual(gamescope_metrics.read_steam_frametime(home, "1091500"), 16.7)
            sample.assert_called_with(home, 1091500)
            self.assertEqual(gamescope_metrics.read_steam_frametime(home, "-242065055"), 16.7)
            sample.assert_called_with(home, 4052902241)
            sample.reset_mock()
            self.assertIsNone(gamescope_metrics.read_steam_frametime(home, "4294967296"))
            sample.assert_not_called()

    def test_reads_only_the_focused_game_for_the_requested_flatpak(self):
        output = (
            "GAMESCOPE_FOCUSED_APP_GFX(CARDINAL) = 4052902241\n"
            "GAMESCOPE_FOCUSABLE_WINDOWS(CARDINAL) = 1, 769, 2, 3, 4052902241, 4\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            fake_proc = home / "proc"
            (fake_proc / "4").mkdir(parents=True)
            (fake_proc / "4" / "environ").write_bytes(b"LSFGVK_PROFILE=flatpak:com.example.Launcher\0")
            with patch.object(gamescope_metrics.subprocess, "run", return_value=types.SimpleNamespace(stdout=output)), \
                    patch.object(gamescope_metrics, "Path", return_value=fake_proc), \
                    patch.object(gamescope_metrics, "read_frametime", return_value=16.7) as sample:
                self.assertIsNone(gamescope_metrics.read_flatpak_frametime(home, "com.example.Other"))
                sample.assert_not_called()
                self.assertEqual(gamescope_metrics.read_flatpak_frametime(home, "com.example.Launcher"), 16.7)
                sample.assert_called_once_with(home, 4052902241)

    def _sample(self, intervals, wait_for_more=False):
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
                request = event(4, 6, struct.pack("=I", app_id))
                self.assertEqual(stream.read(12), request)
                connection.sendall(
                    event(4, 3, struct.pack("=III", 41, 10_000_000, 0))
                    + event(4, 3, struct.pack("=III", app_id, 900_000_000, 0))
                )
                for frametime in intervals:
                    self.assertEqual(stream.read(12), request)
                    connection.sendall(event(4, 3, struct.pack("=III", app_id, frametime, 0)))
                if wait_for_more:
                    self.assertEqual(stream.read(12), request)
                    self.assertEqual(stream.read(1), b"")

        with tempfile.TemporaryDirectory() as tmp, socket.socket(socket.AF_UNIX) as listener:
            path = Path(tmp) / "gamescope-0"
            listener.bind(str(path))
            listener.listen()
            with ThreadPoolExecutor(max_workers=1) as executor:
                server = executor.submit(serve, listener)
                deadline = time.monotonic() + (0.5 if wait_for_more else 2)
                frametime = _read_compositor(path, app_id, deadline)
                server.result()
                return frametime

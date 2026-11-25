"""Read per-app presentation timing from Gamescope's Wayland control protocol."""

import os
import re
import socket
import stat
import struct
import subprocess
import time
from pathlib import Path


_WORDS = struct.Struct("=II")
_CONTROL = b"gamescope_control\0"
_VERSION = 6
_FRAMES_PER_SAMPLE = 12


def _send(connection: socket.socket, object_id: int, opcode: int, body: bytes) -> None:
    connection.sendall(_WORDS.pack(object_id, ((8 + len(body)) << 16) | opcode) + body)


def _events(connection: socket.socket, deadline: float):
    buffer = bytearray()
    while (remaining := deadline - time.monotonic()) > 0:
        connection.settimeout(remaining)
        chunk = connection.recv(4096)
        if not chunk:
            return
        buffer.extend(chunk)
        while len(buffer) >= _WORDS.size:
            object_id, header = _WORDS.unpack_from(buffer)
            size = header >> 16
            if size < _WORDS.size or size % 4:
                raise ValueError("Invalid Wayland event size")
            if len(buffer) < size:
                break
            body = bytes(buffer[_WORDS.size:size])
            del buffer[:size]
            yield object_id, header & 0xffff, body


def _read_compositor(path: Path, app_id: int, deadline: float) -> float | None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(max(0.0, deadline - time.monotonic()))
        connection.connect(str(path))
        # wl_display.get_registry (new object 2) followed by wl_display.sync (callback 3).
        _send(connection, 1, 1, struct.pack("=I", 2))
        _send(connection, 1, 0, struct.pack("=I", 3))
        events = _events(connection, deadline)
        for object_id, opcode, body in events:
            if object_id == 3 or (object_id == 1 and opcode == 0):
                return None
            if object_id != 2 or opcode != 0 or len(body) < 12:
                continue
            name, length = _WORDS.unpack_from(body)
            version_offset = 8 + ((length + 3) & ~3)
            if body[8:8 + length] != _CONTROL or len(body) != version_offset + 4:
                continue
            if struct.unpack_from("=I", body, version_offset)[0] < _VERSION:
                continue
            # wl_registry.bind (new object 4), then gamescope_control.request_app_performance_stats.
            binding = _WORDS.pack(name, length) + _CONTROL.ljust(version_offset - 8, b"\0")
            _send(connection, 2, 0, binding + _WORDS.pack(_VERSION, 4))
            request = struct.pack("=I", app_id)
            _send(connection, 4, 6, request)
            break
        else:
            return None

        frames = []
        first_frame = True
        try:
            for object_id, opcode, body in events:
                if object_id == 1 and opcode == 0:
                    return None
                if object_id != 4 or opcode != 3 or len(body) != 12:
                    continue
                reported_app, low, high = struct.unpack("=III", body)
                if reported_app != app_id:
                    continue
                frametime_ns = (high << 32) | low
                # The first interval can include time before the game resumed.
                if not first_frame and 0 < frametime_ns < 10_000_000_000:
                    frames.append(frametime_ns)
                first_frame = False
                if len(frames) >= _FRAMES_PER_SAMPLE or time.monotonic() >= deadline:
                    break
                _send(connection, 4, 6, request)
        except TimeoutError:
            pass
        return sum(frames) / (len(frames) * 1_000_000) if frames else None


def read_frametime(user_home: Path, app_id: object) -> float | None:
    if type(app_id) is not int or not 0 < app_id <= 0xffffffff:
        return None
    try:
        uid = user_home.stat().st_uid
        runtime_dir = Path("/run/user") / str(uid)
        candidates = sorted(runtime_dir.glob("gamescope-*"))
    except OSError:
        return None
    deadline = time.monotonic() + 0.8
    for path in candidates:
        if time.monotonic() >= deadline:
            break
        slot = path.name.removeprefix("gamescope-")
        if not slot.isascii() or not slot.isdecimal():
            continue
        try:
            if not stat.S_ISSOCK(path.lstat().st_mode):
                continue
            value = _read_compositor(path, app_id, min(deadline, time.monotonic() + 0.5))
        except (OSError, ValueError):
            continue
        if value is not None:
            return value
    return None


def read_steam_frametime(user_home: Path, steam_app_id: str) -> float | None:
    if not isinstance(steam_app_id, str) or not re.fullmatch(r"-?[0-9]{1,10}", steam_app_id):
        return None
    app_id = int(steam_app_id)
    if not -0x80000000 <= app_id <= 0xffffffff:
        return None
    return read_frametime(user_home, app_id & 0xffffffff)


def read_flatpak_frametime(user_home: Path, flatpak_app_id: str) -> float | None:
    try:
        uid = user_home.stat().st_uid
        output = subprocess.run(
            ["xprop", "-display", ":0", "-root", "GAMESCOPE_FOCUSED_APP_GFX", "GAMESCOPE_FOCUSABLE_WINDOWS"],
            env={**os.environ, "XDG_RUNTIME_DIR": f"/run/user/{uid}"},
            capture_output=True, text=True, check=True, timeout=0.5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None

    properties = {line.split("(", 1)[0]: line.partition("=")[2].strip() for line in output.splitlines()}
    focused = properties.get("GAMESCOPE_FOCUSED_APP_GFX", "")
    if not re.fullmatch(r"[0-9]{1,10}", focused) or int(focused) in (0, 769):
        return None

    windows = re.findall(r"[0-9]+", properties.get("GAMESCOPE_FOCUSABLE_WINDOWS", ""))
    profile = f"LSFGVK_PROFILE=flatpak:{flatpak_app_id}".encode()
    for _, app_id, pid in zip(windows[0::3], windows[1::3], windows[2::3]):
        if app_id != focused:
            continue
        proc = Path("/proc") / pid
        try:
            if proc.stat().st_uid != uid or profile not in (proc / "environ").read_bytes().split(b"\0"):
                continue
        except OSError:
            continue
        return read_frametime(user_home, int(focused))
    return None

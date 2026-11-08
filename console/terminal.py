"""Talking to the real terminal: raw keys (with key-up on Windows), size, output.

Everything here opens the console device directly (``CONIN$``/``CONOUT$`` on
Windows, ``/dev/tty`` elsewhere) instead of using ``sys.stdin``/``sys.stdout``,
because inside Hermes those belong to prompt_toolkit and may be redirected.
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from dataclasses import dataclass

IS_WINDOWS = sys.platform == "win32"

ALT_ON = b"\x1b[?1049h\x1b[?25l\x1b[2J\x1b[H"
ALT_OFF = b"\x1b[0m\x1b[?25h\x1b[?1049l"
SYNC_BEGIN = b"\x1b[?2026h"
SYNC_END = b"\x1b[?2026l"

# How long a key stays "down" on terminals that cannot report key-up.
SYNTH_HOLD_S = 0.18


@dataclass
class Caps:
    sixel: bool = False
    cell_w: int = 10
    cell_h: int = 20
    cols: int = 80
    rows: int = 24


class TerminalError(RuntimeError):
    pass


_DA1 = re.compile(rb"\x1b\[\?([0-9;]+)c")
_CELL = re.compile(rb"\x1b\[6;(\d+);(\d+)t")
_TEXT = re.compile(rb"\x1b\[8;(\d+);(\d+)t")


# --------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------

_VK_NAMES = {
    0x20: "space", 0x26: "up", 0x28: "down", 0x25: "left", 0x27: "right",
    0x0D: "enter", 0x1B: "esc", 0x08: "backspace", 0x09: "tab",
}
_MODIFIER_VKS = {0x10, 0x11, 0x12, 0x14, 0x5B, 0x5C, 0x90, 0x91}


class _WinBackend:
    real_key_release = True

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes as wt

        self.ct = ctypes
        self.wt = wt
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k32 = k32
        k32.CreateFileW.restype = ctypes.c_void_p
        k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.c_void_p]
        k32.GetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.DWORD)]
        k32.SetConsoleMode.argtypes = [ctypes.c_void_p, wt.DWORD]
        k32.WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
        k32.GetNumberOfConsoleInputEvents.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.DWORD)]
        k32.FlushConsoleInputBuffer.argtypes = [ctypes.c_void_p]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]

        class KEY_EVENT_RECORD(ctypes.Structure):
            _fields_ = [("bKeyDown", wt.BOOL), ("wRepeatCount", wt.WORD), ("wVirtualKeyCode", wt.WORD),
                        ("wVirtualScanCode", wt.WORD), ("uChar", wt.WCHAR), ("dwControlKeyState", wt.DWORD)]

        class _Event(ctypes.Union):
            _fields_ = [("KeyEvent", KEY_EVENT_RECORD), ("pad", ctypes.c_byte * 16)]

        class INPUT_RECORD(ctypes.Structure):
            _fields_ = [("EventType", wt.WORD), ("Event", _Event)]

        class COORD(ctypes.Structure):
            _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

        class SMALL_RECT(ctypes.Structure):
            _fields_ = [("Left", ctypes.c_short), ("Top", ctypes.c_short), ("Right", ctypes.c_short), ("Bottom", ctypes.c_short)]

        class CSBI(ctypes.Structure):
            _fields_ = [("dwSize", COORD), ("dwCursorPosition", COORD), ("wAttributes", wt.WORD),
                        ("srWindow", SMALL_RECT), ("dwMaximumWindowSize", COORD)]

        k32.GetConsoleScreenBufferInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(CSBI)]
        k32.ReadConsoleInputW.argtypes = [ctypes.c_void_p, ctypes.POINTER(INPUT_RECORD), wt.DWORD, ctypes.POINTER(wt.DWORD)]
        self._INPUT_RECORD = INPUT_RECORD
        self._CSBI = CSBI

        GENERIC_RW = 0xC0000000
        SHARE_RW = 3
        OPEN_EXISTING = 3
        self.hin = k32.CreateFileW("CONIN$", GENERIC_RW, SHARE_RW, None, OPEN_EXISTING, 0, None)
        self.hout = k32.CreateFileW("CONOUT$", GENERIC_RW, SHARE_RW, None, OPEN_EXISTING, 0, None)
        invalid = ctypes.c_void_p(-1).value
        if self.hin in (None, invalid) or self.hout in (None, invalid):
            raise TerminalError("no console attached")
        self._old_in = wt.DWORD()
        self._old_out = wt.DWORD()

    def enter(self) -> None:
        k32 = self.k32
        k32.GetConsoleMode(self.hin, self.ct.byref(self._old_in))
        k32.GetConsoleMode(self.hout, self.ct.byref(self._old_out))
        # ENABLE_PROCESSED_OUTPUT | ENABLE_VIRTUAL_TERMINAL_PROCESSING | DISABLE_NEWLINE_AUTO_RETURN
        k32.SetConsoleMode(self.hout, 0x0001 | 0x0004 | 0x0008)
        # Raw keys: no line input, no echo, no Ctrl+C processing, no VT translation.
        k32.SetConsoleMode(self.hin, 0x0080)
        k32.FlushConsoleInputBuffer(self.hin)

    def leave(self) -> None:
        k32 = self.k32
        k32.FlushConsoleInputBuffer(self.hin)
        k32.SetConsoleMode(self.hin, self._old_in.value)
        k32.SetConsoleMode(self.hout, self._old_out.value)

    def close(self) -> None:
        self.k32.CloseHandle(self.hin)
        self.k32.CloseHandle(self.hout)

    def write(self, data: bytes) -> None:
        wt, ct = self.wt, self.ct
        view = memoryview(data)
        while len(view):
            written = wt.DWORD(0)
            chunk = bytes(view[:65536])
            if not self.k32.WriteFile(self.hout, chunk, len(chunk), ct.byref(written), None):
                raise TerminalError("console write failed")
            view = view[written.value or len(chunk):]

    def size(self) -> tuple[int, int]:
        info = self._CSBI()
        self.k32.GetConsoleScreenBufferInfo(self.hout, self.ct.byref(info))
        w = info.srWindow
        return max(1, w.Right - w.Left + 1), max(1, w.Bottom - w.Top + 1)

    def read_raw_chars(self) -> str:
        """Drain pending key events and return their characters (for query replies)."""
        out = []
        for ev in self._pending():
            if ev.EventType == 1 and ev.Event.KeyEvent.bKeyDown and ev.Event.KeyEvent.uChar != "\0":
                out.append(ev.Event.KeyEvent.uChar)
        return "".join(out)

    def _pending(self):
        wt, ct = self.wt, self.ct
        count = wt.DWORD(0)
        self.k32.GetNumberOfConsoleInputEvents(self.hin, ct.byref(count))
        n = min(count.value, 256)
        if not n:
            return []
        buf = (self._INPUT_RECORD * n)()
        got = wt.DWORD(0)
        self.k32.ReadConsoleInputW(self.hin, buf, n, ct.byref(got))
        return list(buf[: got.value])

    def poll(self) -> list[tuple[str, bool]]:
        events: list[tuple[str, bool]] = []
        for ev in self._pending():
            if ev.EventType != 1:
                continue
            k = ev.Event.KeyEvent
            vk = k.wVirtualKeyCode
            down = bool(k.bKeyDown)
            if vk in _MODIFIER_VKS:
                continue
            name = _VK_NAMES.get(vk)
            if name is None:
                ch = k.uChar
                if ch == "\x03":
                    name = "esc"  # Ctrl+C leaves, it must never kill Hermes
                    down = True
                elif ch and ch != "\0" and ch.isprintable():
                    name = ch.lower()
                else:
                    continue
            events.append((name, down))
        return events


# --------------------------------------------------------------------------
# POSIX
# --------------------------------------------------------------------------

_ESC_KEYS = {b"[A": "up", b"[B": "down", b"[C": "right", b"[D": "left", b"OA": "up", b"OB": "down", b"OC": "right", b"OD": "left"}


class _PosixBackend:
    real_key_release = False

    def __init__(self) -> None:
        try:
            self.fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
        except OSError as exc:
            raise TerminalError("no controlling terminal") from exc
        self._old = None

    def enter(self) -> None:
        import termios
        import tty

        self._old = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)

    def leave(self) -> None:
        import termios

        if self._old is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self._old)

    def close(self) -> None:
        os.close(self.fd)

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        while len(view):
            n = os.write(self.fd, view)
            view = view[n:]

    def size(self) -> tuple[int, int]:
        s = os.get_terminal_size(self.fd)
        return s.columns, s.lines

    def _read(self, timeout: float = 0.0) -> bytes:
        import select

        out = b""
        while select.select([self.fd], [], [], timeout)[0]:
            chunk = os.read(self.fd, 4096)
            if not chunk:
                break
            out += chunk
            timeout = 0.0
        return out

    def read_raw_chars(self) -> str:
        return self._read(0.0).decode("latin-1")

    def poll(self) -> list[tuple[str, bool]]:
        data = self._read(0.0)
        events: list[tuple[str, bool]] = []
        i = 0
        while i < len(data):
            b = data[i : i + 1]
            if b == b"\x1b":
                seq = data[i + 1 : i + 3]
                if seq in _ESC_KEYS:
                    events.append((_ESC_KEYS[seq], True))
                    i += 3
                    continue
                events.append(("esc", True))
                i += 1
                continue
            if b == b" ":
                events.append(("space", True))
            elif b in (b"\r", b"\n"):
                events.append(("enter", True))
            elif b == b"\x03":
                events.append(("esc", True))
            elif b == b"\x7f":
                events.append(("backspace", True))
            elif b.isalnum() or b in b"-_.":
                events.append((b.decode().lower(), True))
            i += 1
        return events


# --------------------------------------------------------------------------


class Terminal:
    """Context manager owning the screen for the duration of a game."""

    def __init__(self, backend=None) -> None:
        self.backend = backend or (_WinBackend() if IS_WINDOWS else _PosixBackend())
        self.real_key_release = self.backend.real_key_release
        self.caps = Caps()
        self._synth: dict[str, float] = {}
        self._cv = threading.Condition()
        self._queue: list[tuple[bytes, bool]] = []
        self._writer: threading.Thread | None = None
        self._stop = False
        self.dropped = 0

    def __enter__(self) -> "Terminal":
        self.backend.enter()
        self.write(ALT_ON)
        self.caps = self._probe()
        return self

    def __exit__(self, *exc) -> None:
        try:
            self._drain()
            self.write(ALT_OFF)
        finally:
            self.backend.leave()
            self.backend.close()
    
    def write(self, data: bytes) -> None:
        self.backend.write(data)

    # -- background writer ----------------------------------------------------
    # Writing a sixel frame can block for several milliseconds while the terminal
    # digests it. The OS call releases the GIL, so a writer thread lets the next
    # frame be computed meanwhile. A queued picture that has not been sent yet is
    # replaced by a newer one (it would be overpainted anyway); anything marked
    # as not droppable (clears, text) is always delivered, in order.

    def send(self, data: bytes, droppable: bool = False) -> None:
        with self._cv:
            if self._writer is None:
                self._writer = threading.Thread(target=self._write_loop, name="console-writer", daemon=True)
                self._writer.start()
            if droppable and self._queue and self._queue[-1][1]:
                self._queue[-1] = (data, True)
                self.dropped += 1
            else:
                self._queue.append((data, droppable))
            self._cv.notify()

    def _write_loop(self) -> None:
        while True:
            with self._cv:
                while not self._queue and not self._stop:
                    self._cv.wait()
                if not self._queue:
                    return
                data, _ = self._queue.pop(0)
            try:
                self.backend.write(data)
            except Exception:
                with self._cv:
                    self._queue.clear()
                    self._stop = True
                return

    def _drain(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify()
        if self._writer is not None:
            self._writer.join(timeout=2.0)
            self._writer = None

    def size(self) -> tuple[int, int]:
        return self.backend.size()

    # -- capability probe ---------------------------------------------------

    def _probe(self) -> Caps:
        caps = Caps()
        caps.cols, caps.rows = self.size()
        env = os.environ.get("CONSOLE_SIXEL", "").strip().lower()
        self.write(b"\x1b[c\x1b[16t\x1b[18t")
        reply = ""
        deadline = time.monotonic() + 0.6
        while time.monotonic() < deadline:
            reply += self.backend.read_raw_chars()
            if "c" in reply and reply.count("t") >= 2:
                break
            time.sleep(0.01)
        data = reply.encode("latin-1", "ignore")
        # More than one layer may answer DA1 (the console host, then the terminal); any
        # reply that lists attribute 4 means sixel is available.
        for m in _DA1.finditer(data):
            if b"4" in m.group(1).split(b";"):
                caps.sixel = True
        m = _CELL.search(data)
        if m:
            caps.cell_h, caps.cell_w = int(m.group(1)), int(m.group(2))
        if env in {"1", "true", "yes", "on"}:
            caps.sixel = True
        elif env in {"0", "false", "no", "off"}:
            caps.sixel = False
        return caps

    # -- keys ---------------------------------------------------------------

    def poll_keys(self, now: float) -> list[tuple[str, bool]]:
        """Pending key events as ``(name, pressed)``; synthesises key-up where needed."""
        events = self.backend.poll()
        if self.real_key_release:
            return events
        out = []
        for name, _down in events:
            out.append((name, True))
            if name in ("space", "up", "down"):
                self._synth[name] = now + SYNTH_HOLD_S
        for name, until in list(self._synth.items()):
            if now >= until:
                out.append((name, False))
                del self._synth[name]
        return out

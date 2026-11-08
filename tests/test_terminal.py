"""Terminal logic that does not need a real console: probing, key-up, the writer."""

import threading
import time

from console.terminal import Terminal


class FakeBackend:
    real_key_release = False

    def __init__(self, replies="", keys=()):
        self.replies = replies
        self.keys = list(keys)
        self.written = []
        self.gate = threading.Event()
        self.gate.set()

    def enter(self): ...
    def leave(self): ...
    def close(self): ...

    def write(self, data):
        self.gate.wait(2)
        self.written.append(data)

    def size(self):
        return 120, 30

    def read_raw_chars(self):
        out, self.replies = self.replies, ""
        return out

    def poll(self):
        out, self.keys = self.keys, []
        return out


def test_probe_reads_sixel_and_cell_size():
    be = FakeBackend("\x1b[?61;4;6;7c\x1b[6;18;9t\x1b[8;30;120t")
    with Terminal(be) as t:
        assert t.caps.sixel
        assert (t.caps.cell_w, t.caps.cell_h) == (9, 18)


def test_any_da1_reply_with_sixel_counts():
    # a console host may answer first with a plain VT100 reply
    be = FakeBackend("\x1b[?1;0c\x1b[?61;4c\x1b[6;20;10t\x1b[8;30;120t")
    with Terminal(be) as t:
        assert t.caps.sixel


def test_no_sixel_when_the_terminal_does_not_say_so(monkeypatch):
    monkeypatch.delenv("CONSOLE_SIXEL", raising=False)
    be = FakeBackend("\x1b[?1;0c\x1b[6;20;10t\x1b[8;30;120t")
    with Terminal(be) as t:
        assert not t.caps.sixel


def test_sixel_can_be_forced(monkeypatch):
    monkeypatch.setenv("CONSOLE_SIXEL", "1")
    with Terminal(FakeBackend("")) as t:
        assert t.caps.sixel


def test_key_release_is_synthesised_when_the_terminal_cannot_report_it():
    be = FakeBackend("\x1b[?61;4c\x1b[6;20;10t\x1b[8;30;120t", keys=[("space", True)])
    with Terminal(be) as t:
        assert t.poll_keys(10.0) == [("space", True)]
        assert t.poll_keys(10.05) == []
        assert t.poll_keys(10.5) == [("space", False)]


def test_real_key_up_passes_straight_through():
    be = FakeBackend("\x1b[?61;4c", keys=[("space", True), ("space", False)])
    be.real_key_release = True
    with Terminal(be) as t:
        assert t.poll_keys(1.0) == [("space", True), ("space", False)]


def test_writer_replaces_unsent_pictures_but_never_drops_other_output():
    be = FakeBackend("\x1b[?61;4c")
    with Terminal(be) as t:
        be.gate.clear()  # the terminal is busy digesting something
        t.send(b"first", droppable=True)
        time.sleep(0.1)  # the writer thread picks it up and blocks on the gate
        t.send(b"pic-1", droppable=True)
        t.send(b"pic-2", droppable=True)  # supersedes pic-1
        t.send(b"clear", droppable=False)
        t.send(b"pic-3", droppable=True)
        be.gate.set()
    sent = [d for d in be.written if d in (b"first", b"pic-1", b"pic-2", b"clear", b"pic-3")]
    assert sent == [b"first", b"pic-2", b"clear", b"pic-3"]

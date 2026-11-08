import json

from console import state


def test_high_score_round_trip_and_only_ever_goes_up(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert state.load_high_score("dino") == 0
    state.save_high_score("dino", 5000)
    assert state.load_high_score("dino") == 5000
    state.save_high_score("dino", 100)  # a worse run never overwrites
    assert state.load_high_score("dino") == 5000
    data = json.loads((tmp_path / "console.json").read_text())
    assert data["high_scores"]["dino"] == 5000


def test_corrupt_state_file_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "console.json").write_text("{not json")
    assert state.load_high_score("dino") == 0
    state.save_high_score("dino", 7)
    assert state.load_high_score("dino") == 7


def test_hermes_activity_flags():
    a = state.HermesActivity()
    assert a.snapshot() == (False, False)
    a.started()
    assert a.snapshot() == (True, False)
    a.ended(completed=True)
    assert a.snapshot() == (False, True)
    a.acknowledge()
    assert a.snapshot() == (False, False)
    a.started()
    a.ended(completed=False)
    assert a.snapshot() == (False, False)

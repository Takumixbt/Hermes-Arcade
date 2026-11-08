from console.games.dino import sound


def test_sound_assets_are_real_wavs():
    import wave

    for name in sound.NAMES.values():
        with wave.open(str(sound.ASSETS / name)) as w:
            assert w.getnframes() > 1000


def test_sound_can_be_muted_and_never_raises(monkeypatch):
    monkeypatch.setenv("CONSOLE_SOUND", "0")
    s = sound.Sounds()
    s.play("press")
    s.play("not-a-sound")
    assert not s._on

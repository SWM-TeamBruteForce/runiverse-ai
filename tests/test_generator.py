import subprocess
import sys
import wave

import pytest

from runiverse_music.config import load_settings
from runiverse_music.generator import (
    AceStepGenerator, FakeGenerator, GenerationError, check_audio_file, make_generator,
)
from runiverse_music.schemas import MusicRequest

REQ = MusicRequest(request_id="r1", caption="c", bpm=170, duration=30, seed=42)
BASE_ENV = {"STORAGE": "local", "LOCAL_OUTPUT_DIR": "/tmp/o"}


def test_fake_generator_writes_wav(tmp_path):
    g = FakeGenerator()
    g.load()
    result = g.generate(REQ, tmp_path)
    assert result.audio_path == tmp_path / "r1.wav"
    with wave.open(str(result.audio_path)) as w:
        assert w.getframerate() == 48000
        assert w.getnchannels() == 2
    assert (result.sample_rate, result.channels) == (48000, 2)
    assert result.size_bytes == result.audio_path.stat().st_size > 0
    assert g.manifest == {"generator": "fake"}


def test_check_audio_file_missing(tmp_path):
    with pytest.raises(GenerationError):
        check_audio_file(tmp_path / "none.wav")


def test_check_audio_file_empty(tmp_path):
    path = tmp_path / "e.wav"
    path.write_bytes(b"")
    with pytest.raises(GenerationError):
        check_audio_file(path)


def test_make_generator():
    assert isinstance(make_generator(load_settings({**BASE_ENV, "GENERATOR": "fake"})), FakeGenerator)
    assert isinstance(make_generator(load_settings({**BASE_ENV, "GENERATOR": "acestep"})), AceStepGenerator)


def test_acestep_generate_before_load_fails(tmp_path):
    g = AceStepGenerator(load_settings(BASE_ENV))
    with pytest.raises(GenerationError, match="로드"):
        g.generate(REQ, tmp_path)


def test_module_import_does_not_load_torch():
    code = "import sys, runiverse_music.generator; print('torch' in sys.modules, 'acestep' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "False False"

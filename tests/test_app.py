import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runiverse_music.app import build_app, create_app
from runiverse_music.config import load_settings
from runiverse_music.generator import CudaOutOfMemory, FakeGenerator, GenerationError
from runiverse_music.storage import LocalStorage, StorageError

REQ = {"request_id": "req-1", "caption": "running music", "bpm": 170, "duration": 30, "seed": 42}


@pytest.fixture
def settings(tmp_path):
    return load_settings({"GENERATOR": "fake", "STORAGE": "local", "LOCAL_OUTPUT_DIR": str(tmp_path / "out")})


def make_client(settings, generator=None, storage=None):
    app = create_app(settings, generator or FakeGenerator(), storage or LocalStorage(settings.local_output_dir))
    return TestClient(app)


def post(client, payload):
    return client.post("/invocations", content=json.dumps(payload), headers={"Content-Type": "application/json"})


def test_ping_503_before_startup(settings):
    client = make_client(settings)  # with 없이 → lifespan 미실행
    assert client.get("/ping").status_code == 503


def test_ping_200_after_load(settings):
    with make_client(settings) as client:
        assert client.get("/ping").status_code == 200


def test_load_failure_aborts_startup(settings):
    class Broken(FakeGenerator):
        def load(self):
            raise GenerationError("no cuda")

    with pytest.raises(GenerationError):
        with make_client(settings, Broken()):
            pass


def test_invocation_success(settings):
    with make_client(settings) as client:
        r = post(client, REQ)
    assert r.status_code == 200
    data = r.json()
    assert data["request_id"] == "req-1"
    assert data["audio_s3_key"] == "music/outputs/req-1/req-1.wav"
    assert data["sample_rate"] == 48000
    assert data["bytes"] > 0
    assert data["manifest"]["generator"] == "fake"
    assert data["manifest"]["request"]["bpm"] == 170
    assert (settings.local_output_dir / data["audio_s3_key"]).is_file()


def test_same_request_twice_overwrites(settings):
    with make_client(settings) as client:
        first, second = post(client, REQ), post(client, REQ)
    assert first.status_code == second.status_code == 200
    assert first.json()["audio_s3_key"] == second.json()["audio_s3_key"]


@pytest.mark.parametrize(
    "payload",
    [{**REQ, "bpm": 500}, {**REQ, "duration": 121}, {**REQ, "request_id": "../x"}, {**REQ, "instrumental": False}],
)
def test_invalid_request_400(settings, payload):
    with make_client(settings) as client:
        r = post(client, payload)
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_request"


def test_malformed_json_400(settings):
    with make_client(settings) as client:
        r = client.post("/invocations", content=b"{oops", headers={"Content-Type": "application/json"})
    assert r.status_code == 400


def test_oom_then_recovers(settings):
    class OomOnce(FakeGenerator):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def generate(self, request, out_dir):
            self.calls += 1
            if self.calls == 1:
                raise CudaOutOfMemory("CUDA out of memory")
            return super().generate(request, out_dir)

    with make_client(settings, OomOnce()) as client:
        first, second = post(client, REQ), post(client, REQ)
    assert first.status_code == 500
    assert first.json()["error"] == "cuda_oom"
    assert second.status_code == 200


def test_generation_failure_500(settings):
    class Failing(FakeGenerator):
        def generate(self, request, out_dir):
            raise GenerationError("empty file")

    with make_client(settings, Failing()) as client:
        r = post(client, REQ)
    assert r.status_code == 500
    assert r.json() == {"error": "generation_failed", "request_id": "req-1", "message": "empty file"}


def test_upload_failure_500(settings):
    class BrokenStorage:
        def upload(self, local_path, key):
            raise StorageError("s3 down")

    with make_client(settings, storage=BrokenStorage()) as client:
        r = post(client, REQ)
    assert r.status_code == 500
    assert r.json()["error"] == "upload_failed"


def test_build_app_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GENERATOR", "fake")
    monkeypatch.setenv("STORAGE", "local")
    monkeypatch.setenv("LOCAL_OUTPUT_DIR", str(tmp_path))
    assert isinstance(build_app(), FastAPI)

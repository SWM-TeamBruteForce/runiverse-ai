import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runiverse_music.analyzer import FakeAnalyzer
from runiverse_music.app import build_app, create_app
from runiverse_music.config import load_settings
from runiverse_music.generator import FakeGenerator, GenerationError
from runiverse_music.storage import LocalStorage, StorageError
from test_card_request import CARD, card

BASE_KEY = "music/outputs/123/TRAVEL/101/base.wav"


@pytest.fixture
def settings(tmp_path):
    return load_settings(
        {"ANALYZER": "fake", "GENERATOR": "fake", "STORAGE": "local", "LOCAL_OUTPUT_DIR": str(tmp_path / "out")}
    )


def make_client(settings, analyzer=None, generator=None, storage=None):
    app = create_app(
        settings,
        analyzer or FakeAnalyzer(),
        generator or FakeGenerator(),
        storage or LocalStorage(settings.local_output_dir),
    )
    return TestClient(app)


def post(client, payload):
    return client.post("/invocations", content=json.dumps(payload), headers={"Content-Type": "application/json"})


def test_ping_503_before_startup(settings):
    client = make_client(settings)  # with 없이 → lifespan 미실행
    assert client.get("/ping").status_code == 503


def test_ping_200_after_load(settings):
    with make_client(settings) as client:
        assert client.get("/ping").status_code == 200


def test_generator_load_failure_aborts_startup(settings):
    class Broken(FakeGenerator):
        def load(self):
            raise GenerationError("no cuda")

    with pytest.raises(GenerationError):
        with make_client(settings, generator=Broken()):
            pass


def test_analyzer_load_failure_aborts_startup(settings):
    class Broken(FakeAnalyzer):
        def load(self):
            raise RuntimeError("no weights")

    with pytest.raises(RuntimeError):
        with make_client(settings, analyzer=Broken()):
            pass


def test_invocation_returns_card_result(settings):
    with make_client(settings) as client:
        r = post(client, card())
    assert r.status_code == 200
    data = r.json()
    assert (data["record_card_id"], data["status"], data["error"]) == (101, "SUCCEEDED", None)
    assert data["base_music"]["s3_key"] == BASE_KEY
    assert [p["photo_index"] for p in data["photo_music"]] == [0]
    assert data["manifest"]["prompt_mapping_version"] == "v1"
    assert (settings.local_output_dir / BASE_KEY).is_file()


def test_same_request_twice_overwrites(settings):
    with make_client(settings) as client:
        first, second = post(client, card()), post(client, card())
    assert first.status_code == second.status_code == 200
    assert first.json()["base_music"]["s3_key"] == second.json()["base_music"]["s3_key"]


def test_invalid_request_body_keeps_ids_when_readable(settings):
    with make_client(settings) as client:
        r = post(client, card(activity_type="RUN"))
    assert r.status_code == 400
    body = r.json()
    assert (body["user_id"], body["record_card_id"], body["status"]) == (123, 101, "FAILED")
    assert body["error"]["detail"][0]["loc"] == ["activity_type"]


@pytest.mark.parametrize(
    "payload",
    [
        card(activity_type="RUN"),
        card(bpm=170),
        card(photos=[{"caption": "image_key 없음"}]),
        {k: v for k, v in CARD.items() if k != "user_id"},
    ],
)
def test_invalid_request_400(settings, payload):
    with make_client(settings) as client:
        r = post(client, payload)
    assert r.status_code == 400
    assert (r.json()["status"], r.json()["error"]["code"]) == ("FAILED", "INVALID_REQUEST")


def test_malformed_json_400(settings):
    with make_client(settings) as client:
        r = client.post("/invocations", content=b"{oops", headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert (r.json()["user_id"], r.json()["record_card_id"]) == (None, None)  # 읽을 수 없으면 ID 없음


def test_track_failure_is_500_with_code(settings):
    class Failing(FakeGenerator):
        def generate(self, job, out_dir):
            raise GenerationError("empty file")

    with make_client(settings, generator=Failing()) as client:
        r = post(client, card())
    assert r.status_code == 500  # 실패는 200이 아님 → FailureLocation(async/failures/)
    assert r.json() == {
        "user_id": 123, "record_card_id": 101, "status": "FAILED",
        "error": {"code": "MUSIC_GENERATION_FAILED",
                  "message": "음악 생성 실패: 기본 음악(GenerationError), 사진 0(GenerationError)"},
    }


def test_upload_failure_is_502(settings):
    class BrokenStorage(LocalStorage):
        def upload(self, local_path, key):
            raise StorageError("s3 down")

    with make_client(settings, storage=BrokenStorage(settings.local_output_dir)) as client:
        r = post(client, card())
    assert r.status_code == 502
    assert (r.json()["status"], r.json()["error"]["code"]) == ("FAILED", "MUSIC_UPLOAD_FAILED")


def test_unexpected_error_is_500_then_recovers_without_private_text(settings, caplog):
    review = CARD["memo"]

    class FailsOnce(FakeAnalyzer):
        calls = 0

        def to_gpu(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError(review)  # 예외 메시지에 원문이 섞인 경우

    with caplog.at_level(logging.INFO, logger="runiverse_music"):
        with make_client(settings, analyzer=FailsOnce()) as client:
            first, second = post(client, card()), post(client, card())
    assert first.status_code == 500
    assert first.json()["error"]["code"] == "INTERNAL_ERROR"
    assert second.status_code == 200
    assert review not in caplog.text


def test_build_app_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ANALYZER", "fake")
    monkeypatch.setenv("GENERATOR", "fake")
    monkeypatch.setenv("STORAGE", "local")
    monkeypatch.setenv("LOCAL_OUTPUT_DIR", str(tmp_path))
    assert isinstance(build_app(), FastAPI)

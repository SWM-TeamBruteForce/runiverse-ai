import json
import math

import pytest
from pydantic import ValidationError

from runiverse_music.schemas import MusicRequest, MusicResponse, parse_request

VALID = {
    "request_id": "req-001",
    "caption": "Energetic electronic running music",
    "bpm": 170,
    "duration": 30.0,
    "seed": 42,
    "instrumental": True,
}


def body(**over):
    return json.dumps({**VALID, **over}).encode()


def test_parses_valid_request():
    req = parse_request(body(), max_duration=120)
    assert (req.request_id, req.bpm, req.duration, req.seed) == ("req-001", 170, 30.0, 42)


def test_instrumental_defaults_true():
    data = {k: v for k, v in VALID.items() if k != "instrumental"}
    assert parse_request(json.dumps(data).encode(), 120).instrumental is True


@pytest.mark.parametrize("bpm", [30, 300])
def test_bpm_bounds_ok(bpm):
    assert parse_request(body(bpm=bpm), 120).bpm == bpm


def test_duration_limit_follows_setting():
    assert parse_request(body(duration=200), 300).duration == 200


@pytest.mark.parametrize(
    "over",
    [
        {"bpm": 29},
        {"bpm": 301},
        {"duration": 9.9},
        {"duration": 120.5},
        {"seed": -1},
        {"instrumental": False},
        {"caption": "   "},
        {"caption": "x" * 513},
        {"request_id": ""},
        {"request_id": "../etc"},
        {"request_id": "a/b"},
        {"request_id": "x" * 65},
        {"extra": 1},
    ],
)
def test_rejects_invalid(over):
    with pytest.raises(ValidationError):
        parse_request(body(**over), 120)


def test_rejects_nan_duration():
    with pytest.raises(ValidationError):
        MusicRequest.model_validate({**VALID, "duration": math.nan})


def test_rejects_malformed_json():
    with pytest.raises(ValidationError):
        parse_request(b"{not json", 120)


def test_response_serializes():
    resp = MusicResponse(
        request_id="r", audio_s3_key="k.wav", sample_rate=48000, channels=2,
        actual_duration_seconds=30.0, bytes=10, generation_seconds=1.0,
        peak_torch_allocated_gib=0.0, manifest={"a": 1},
    )
    assert resp.model_dump()["bytes"] == 10

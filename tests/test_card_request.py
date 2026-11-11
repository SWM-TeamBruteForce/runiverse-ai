import copy

import pytest
from pydantic import ValidationError

from runiverse_music.schemas import CardRequest

# 전처리 모듈 문서 1장 입력 예시
CARD = {
    "user_id": 123,
    "record_card_id": 101,
    "memo": "힘들었지만 끝까지 달려서 뿌듯했다",
    "activity_type": "TRAVEL",
    "running_summary": {
        "total_distance": 5000,
        "total_duration": 1800,
        "avg_pace": 360,
        "avg_cadence": 168,
        "start_at": "2026-10-19T06:30:00+09:00",
        "weather_code": 61,
        "temperature": 15.0,
        "total_elevation_gain": 22.0,
    },
    "photos": [
        {
            "image_key": "cards/101/photos/201.jpg",
            "caption": "바닷가에서 찍은 사진",
        }
    ],
}


def card(**over):
    return {**copy.deepcopy(CARD), **over}


def test_parses_doc_example():
    req = CardRequest.model_validate(card())
    assert (req.record_card_id, req.activity_type, req.running_summary.avg_pace, req.running_summary.weather_code) == (
        101, "TRAVEL", 360, 61,
    )
    assert req.photos[0].image_key == "cards/101/photos/201.jpg"


def test_optional_texts_and_metrics_may_be_null_or_missing():
    data = card(memo=None, running_summary={}, photos=[])
    data["photos"] = [{**CARD["photos"][0], "caption": None}]
    req = CardRequest.model_validate(data)
    assert (req.memo, req.running_summary.avg_pace, req.photos[0].caption) == (None, None, None)


def test_photos_default_to_empty():
    data = card()
    del data["photos"]
    assert CardRequest.model_validate(data).photos == []


@pytest.mark.parametrize("photo", [{"caption": "키 없음"}, {"image_key": ""}, {"image_key": "k", "photo_id": 1}])
def test_rejects_invalid_photo(photo):
    with pytest.raises(ValidationError):
        CardRequest.model_validate(card(photos=[photo]))


@pytest.mark.parametrize("activity", ["travel", "RUN", None])
def test_rejects_unknown_activity(activity):
    with pytest.raises(ValidationError):
        CardRequest.model_validate(card(activity_type=activity))


def test_rejects_unknown_top_level_field():
    with pytest.raises(ValidationError):
        CardRequest.model_validate(card(bpm=170))

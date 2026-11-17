import copy

import pytest
from pydantic import ValidationError

from runiverse_music.schemas import CardRequest

# 백엔드 ID는 UUID 문자열 (2026-11-17, test 토픽 실제 메시지 확인)
USER = "7b1e4c2a-1111-4aaa-8bbb-000000000123"
CARD_ID = "0c9d8e7f-2222-4ccc-9ddd-000000000101"
PHOTO_ID = "5a6b7c8d-3333-4eee-afff-000000000201"

# 전처리 모듈 문서 1장 입력 예시 (ID만 UUID)
CARD = {
    "user_id": USER,
    "record_card_id": CARD_ID,
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
            "record_card_image_id": PHOTO_ID,
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
        CARD_ID, "TRAVEL", 360, 61,
    )
    assert (req.photos[0].record_card_image_id, req.photos[0].image_key) == (PHOTO_ID, "cards/101/photos/201.jpg")


def test_keeps_ids_as_sent():
    upper = card(user_id=USER.upper(), record_card_id=CARD_ID.upper())
    req = CardRequest.model_validate(upper)
    assert (req.user_id, req.record_card_id) == (USER.upper(), CARD_ID.upper())  # 대소문자 정리 없이 그대로


# S3 키에 들어가므로 UUID 모양(8-4-4-4-12 16진수)만 받는다
BAD_IDS = [101, "101", "", True, None, CARD_ID + "0", CARD_ID.replace("-", ""), "../" + CARD_ID[3:], "g" + CARD_ID[1:]]


@pytest.mark.parametrize("field", ["user_id", "record_card_id"])
@pytest.mark.parametrize("bad", BAD_IDS)
def test_rejects_non_uuid_card_ids(field, bad):
    with pytest.raises(ValidationError):
        CardRequest.model_validate(card(**{field: bad}))


@pytest.mark.parametrize("bad", BAD_IDS)
def test_rejects_non_uuid_photo_id(bad):
    photo = {**CARD["photos"][0], "record_card_image_id": bad}
    with pytest.raises(ValidationError):
        CardRequest.model_validate(card(photos=[photo]))


def test_optional_texts_and_metrics_may_be_null_or_missing():
    data = card(memo=None, running_summary={}, photos=[])
    data["photos"] = [{**CARD["photos"][0], "caption": None}]
    req = CardRequest.model_validate(data)
    assert (req.memo, req.running_summary.avg_pace, req.photos[0].caption) == (None, None, None)


def test_photos_default_to_empty():
    data = card()
    del data["photos"]
    assert CardRequest.model_validate(data).photos == []


@pytest.mark.parametrize("photo", [
    {"record_card_image_id": PHOTO_ID, "caption": "키 없음"},
    {"record_card_image_id": PHOTO_ID, "image_key": ""},
    {"image_key": "k"},                                   # 사진 ID 누락
    {"record_card_image_id": "abc", "image_key": "k"},
    {"record_card_image_id": PHOTO_ID, "image_key": "k", "photo_id": 1},
])
def test_rejects_invalid_photo(photo):
    with pytest.raises(ValidationError):
        CardRequest.model_validate(card(photos=[photo]))


def test_rejects_duplicate_photo_ids():
    photo = CARD["photos"][0]
    with pytest.raises(ValidationError, match="중복"):
        CardRequest.model_validate(card(photos=[photo, {**photo, "image_key": "cards/101/photos/202.jpg"}]))


@pytest.mark.parametrize("activity", ["travel", "RUN", None])
def test_rejects_unknown_activity(activity):
    with pytest.raises(ValidationError):
        CardRequest.model_validate(card(activity_type=activity))


def test_rejects_unknown_top_level_field():
    with pytest.raises(ValidationError):
        CardRequest.model_validate(card(bpm=170))

import json
import subprocess
import sys

import pytest

from runiverse_music.analyzer import (
    FakeAnalyzer,
    Qwen3VLAnalyzer,
    ReviewAnalysis,
    default_review,
    make_analyzer,
    parse_photo,
    parse_review,
    photo_messages,
    review_messages,
)
from runiverse_music.config import load_settings
from runiverse_music.mapping_rules import EMOTION_DIRECTIONS

REVIEW = {
    "emotion_direction": "성취·환희",
    "emotion_cue": "지친 끝에 찾아온 조용한 성취감",
    "music_cue_en": "quiet pride after a difficult finish",
}


def raw(data):
    return json.dumps(data, ensure_ascii=False)


def test_parse_review_reads_model_json():
    assert parse_review(raw(REVIEW)) == ReviewAnalysis(**REVIEW)


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        raw({**REVIEW, "emotion_direction": "기쁨"}),  # 7종 밖
        raw({**REVIEW, "music_cue_en": "  "}),          # 빈 단서
        raw({k: v for k, v in REVIEW.items() if k != "emotion_cue"}),
        raw(["list"]),
    ],
)
def test_parse_review_returns_none_when_invalid(text):
    assert parse_review(text) is None


def test_default_review_is_calm():
    assert default_review() == ReviewAnalysis("평온·담담함", "담담하고 안정적인 분위기", "a calm and steady mood")


def test_parse_photo_reads_cue():
    assert parse_photo(raw({"music_cue_en": " an open, airy coastal scene "})) == "an open, airy coastal scene"


@pytest.mark.parametrize("text", ["not json", raw({}), raw({"music_cue_en": ""}), raw({"music_cue_en": 3})])
def test_parse_photo_returns_none_when_invalid(text):
    assert parse_photo(text) is None


def test_fake_analyzer_output_passes_parsers():
    fake = FakeAnalyzer()
    assert parse_review(fake.analyze_review("힘들었지만 뿌듯했다", "TRAVEL")) is not None
    assert parse_photo(fake.analyze_photo(b"jpg", "바닷가", "TRAVEL")) is not None


def test_parse_accepts_code_fence_around_json():
    # Qwen 계열은 JSON을 ```json 펜스로 감싸 출력하는 경우가 많다
    assert parse_review(f"```json\n{raw(REVIEW)}\n```") == ReviewAnalysis(**REVIEW)
    assert parse_photo('```json\n{"music_cue_en": "a quiet harbor"}\n```') == "a quiet harbor"


def test_review_messages_carry_review_activity_and_directions():
    [message] = review_messages("힘들었지만 뿌듯했다", "TRAVEL")
    text = message["content"][0]["text"]
    assert message["role"] == "user"
    assert "힘들었지만 뿌듯했다" in text and "a run while traveling" in text
    assert all(direction in text for direction in EMOTION_DIRECTIONS)


def test_photo_messages_put_image_before_text():
    image = object()  # 실제로는 PIL 이미지
    [message] = photo_messages(image, "바닷가에서 찍은 사진", "TRAVEL")
    image_part, text_part = message["content"]
    assert image_part == {"type": "image", "image": image}
    assert "바닷가에서 찍은 사진" in text_part["text"] and "a run while traveling" in text_part["text"]


def test_photo_messages_mark_missing_caption():
    [message] = photo_messages(object(), None, "DAILY")
    assert "사용자 설명: 없음" in message["content"][1]["text"]


def test_make_analyzer():
    env = {"STORAGE": "local", "LOCAL_OUTPUT_DIR": "/tmp/o", "MODEL_DIR": "/models"}
    assert isinstance(make_analyzer(load_settings({**env, "ANALYZER": "fake"})), FakeAnalyzer)
    qwen = make_analyzer(load_settings({**env, "ANALYZER": "qwen"}))
    assert isinstance(qwen, Qwen3VLAnalyzer)  # 모델은 load() 전까지 읽지 않는다
    assert qwen.model_dir.as_posix() == "/models/qwen3-vl"


def test_module_import_does_not_load_torch():
    code = "import sys, runiverse_music.analyzer; print('torch' in sys.modules, 'transformers' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "False False"

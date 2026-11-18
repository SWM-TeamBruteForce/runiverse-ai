from collections import Counter

import pytest

from runiverse_music import mapping_rules
from runiverse_music.mapping_rules import (
    BASE_DURATION_SECONDS,
    DEFAULT_EMOTION_DIRECTION,
    DEFAULT_WEATHER_GROUP,
    EMOTION_DIRECTIONS,
    PHOTO_DURATION_SECONDS,
)
from runiverse_music.music_params import (
    SharedParams,
    bpm_from_pace,
    decide_shared,
    keyscale_for,
    weather_group,
)


# ── 페이스 → BPM ─────────────────────────────────────────────
@pytest.mark.parametrize(
    ("pace", "bpm"),
    [
        (200, 180),  # 3:20/km
        (269, 180),  # 경계 바로 아래
        (270, 170),  # 4:30/km 경계
        (320, 170),  # 5:20/km (문서 예시)
        (359, 170),
        (360, 160),  # 6:00/km 경계
        (449, 160),
        (450, 150),  # 7:30/km 경계
        (900, 150),
    ],
)
def test_bpm_follows_pace_table(pace, bpm):
    assert bpm_from_pace(pace) == bpm


@pytest.mark.parametrize("pace", [None, 0, -10, True, "320"])
def test_bpm_falls_back_to_160_when_pace_invalid(pace):
    assert bpm_from_pace(pace) == 160


# ── WMO 날씨 코드 → 날씨 그룹 ────────────────────────────────
# 문서 표의 구간 경계(시작·끝)를 모두 확인한다
@pytest.mark.parametrize(
    ("codes", "group"),
    [
        ([0, 3, 14, 16, 20, 29], "DRY_OR_RECENT"),
        ([4, 12, 30, 35, 40, 49], "HAZE_OR_FOG"),
        ([50, 61, 67, 80, 82, 91, 92], "RAIN"),
        ([36, 39, 68, 79, 83, 90, 93, 94], "SNOW_OR_ICE"),
        ([13, 17, 19, 95, 99], "STORM_OR_WIND"),
    ],
)
def test_weather_code_maps_to_group(codes, group):
    assert [weather_group(c) for c in codes] == [group] * len(codes)


def test_wmo_codes_0_to_99_split_into_expected_group_sizes():
    # 표에 빈칸이 있으면 기본 그룹(DRY_OR_RECENT)으로 빠져 개수가 어긋난다
    counts = Counter(weather_group(c) for c in range(100))
    assert counts == {
        "DRY_OR_RECENT": 17,  # 00~03(4) + 14~16(3) + 20~29(10)
        "HAZE_OR_FOG": 25,    # 04~12(9) + 30~35(6) + 40~49(10)
        "RAIN": 23,           # 50~67(18) + 80~82(3) + 91~92(2)
        "SNOW_OR_ICE": 26,    # 36~39(4) + 68~79(12) + 83~90(8) + 93~94(2)
        "STORM_OR_WIND": 9,   # 13(1) + 17~19(3) + 95~99(5)
    }


@pytest.mark.parametrize("code", [None, -1, 100, True, "61"])
def test_weather_falls_back_to_default_group(code):
    assert weather_group(code) == DEFAULT_WEATHER_GROUP


# ── 회고 방향 × 날씨 그룹 → 조성 ─────────────────────────────
# 문서 표를 그대로 옮긴 기대값. 표를 바꾸면 여기와 MAPPING_VERSION을 함께 바꾼다
GROUPS = ("DRY_OR_RECENT", "HAZE_OR_FOG", "RAIN", "SNOW_OR_ICE", "STORM_OR_WIND")
EXPECTED_KEYS = {
    "평온·담담함": ("C Major", "F Major", "F Major", "Eb Major", "C Major"),
    "설렘·가벼운 즐거움": ("A Major", "A Major", "D Major", "Eb Major", "D Major"),
    "성취·환희": ("D Major", "D Major", "E Major", "Eb Major", "E Major"),
    "감사·따뜻한 위로": ("G Major", "F Major", "G Major", "Eb Major", "Eb Major"),
    "아쉬움·그리움": ("G Minor", "E Minor", "G Minor", "D Minor", "C Minor"),
    "고뇌·결연함": ("C Minor", "F# Minor", "C Minor", "D Minor", "C Minor"),
    "깊은 슬픔·상실감": ("A Minor", "E Minor", "D Minor", "F Minor", "F Minor"),
}


def test_emotion_directions_are_the_seven_in_doc():
    assert EMOTION_DIRECTIONS == tuple(EXPECTED_KEYS)


@pytest.mark.parametrize("direction", list(EXPECTED_KEYS))
def test_keyscale_table_matches_doc(direction):
    assert tuple(keyscale_for(direction, g) for g in GROUPS) == EXPECTED_KEYS[direction]


def test_doc_example_achievement_in_rain_is_e_major():
    assert keyscale_for("성취·환희", "RAIN") == "E Major"


@pytest.mark.parametrize("direction", [None, "", "기쁨", "성취"])
def test_unknown_direction_uses_default_direction(direction):
    assert DEFAULT_EMOTION_DIRECTION == "평온·담담함"
    assert keyscale_for(direction, "RAIN") == keyscale_for("평온·담담함", "RAIN")


def test_unknown_group_uses_default_group():
    assert keyscale_for("성취·환희", "SUNNY") == keyscale_for("성취·환희", DEFAULT_WEATHER_GROUP)


# ── 카드 공통 설정 ───────────────────────────────────────────
def test_decide_shared_matches_doc_example():
    # 문서 예시: 5:20/km(320초) · 성취·환희 · ww=61(RAIN) → 170 BPM · E Major · 4/4
    shared = decide_shared(avg_pace=320, weather_code=61, emotion_direction="성취·환희")
    assert shared == SharedParams(bpm=170, keyscale="E Major", timesignature="4", instrumental=True)


def test_decide_shared_with_missing_inputs_uses_defaults():
    shared = decide_shared(avg_pace=None, weather_code=None, emotion_direction=None)
    assert (shared.bpm, shared.keyscale) == (160, "C Major")  # 평온·담담함 × DRY_OR_RECENT


def test_shared_params_serialize_to_doc_shape():
    shared = decide_shared(avg_pace=320, weather_code=61, emotion_direction="성취·환희")
    assert shared.to_dict() == {
        "bpm": 170,
        "keyscale": "E Major",
        "timesignature": "4",
        "instrumental": True,
    }


def test_track_durations_are_30_and_10_seconds():
    assert (BASE_DURATION_SECONDS, PHOTO_DURATION_SECONDS) == (30.0, 10.0)


# ── 규칙값 교체 (실험·테스트용) ───────────────────────────────
def test_changing_mapping_rules_changes_result(monkeypatch):
    # music_params는 호출할 때마다 mapping_rules 값을 읽는다 → 값만 바꿔 실험할 수 있다
    monkeypatch.setattr(mapping_rules, "DEFAULT_BPM", 140)
    monkeypatch.setattr(mapping_rules, "TIME_SIGNATURE", "3")
    shared = decide_shared(avg_pace=None, weather_code=61, emotion_direction="성취·환희")
    assert (shared.bpm, shared.timesignature) == (140, "3")

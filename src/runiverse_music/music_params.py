"""카드 공통 음악 수치(BPM·조성·박자) 결정. 모델 호출 없이 규칙표만 조회한다.

규칙값은 mapping_rules.py에 있다. 모듈째 import해 호출 시점에 값을 읽으므로
mapping_rules 값을 바꾸면(monkeypatch 등) 결과에 바로 반영된다.
"""
from dataclasses import asdict, dataclass

from runiverse_music import mapping_rules as rules


def bpm_from_pace(avg_pace) -> int:
    """평균 페이스(초/km) → 카드 공통 BPM.

    정수가 아니거나 0 이하이면 페이스를 알 수 없다고 보고 DEFAULT_BPM을 쓴다.
    """
    # bool은 int의 하위 타입이라 True가 1초로 통과하지 않도록 먼저 걸러 낸다
    if isinstance(avg_pace, bool) or not isinstance(avg_pace, int) or avg_pace <= 0:
        return rules.DEFAULT_BPM
    for upper, bpm in rules.PACE_TO_BPM:
        if avg_pace < upper:
            return bpm
    return rules.SLOWEST_BPM


def weather_group(weather_code) -> str:
    """WMO ww 코드(0~99) → 조성표의 날씨 그룹.

    코드가 없거나 범위 밖이면 DEFAULT_WEATHER_GROUP. 실제 날씨를 지어내지는 않는다
    (그룹은 조성 선택에만 쓰고, caption에 날씨를 쓰지 않는다).
    """
    if isinstance(weather_code, bool) or not isinstance(weather_code, int):
        return rules.DEFAULT_WEATHER_GROUP
    for group, ranges in rules.WEATHER_RANGES:
        if any(start <= weather_code <= end for start, end in ranges):
            return group
    return rules.DEFAULT_WEATHER_GROUP  # 0 미만·99 초과


def keyscale_for(emotion_direction, group) -> str:
    """emotion_direction × 날씨 그룹 → ACE-Step keyscale (예: "E Major").

    목록에 없는 방향은 DEFAULT_EMOTION_DIRECTION, 없는 그룹은 DEFAULT_WEATHER_GROUP으로 본다.
    """
    table = rules.KEYSCALE_TABLE
    row = table.get(emotion_direction) or table[rules.DEFAULT_EMOTION_DIRECTION]
    if group not in rules.WEATHER_GROUPS:
        group = rules.DEFAULT_WEATHER_GROUP
    return row[rules.WEATHER_GROUPS.index(group)]


@dataclass(frozen=True)
class SharedParams:
    """카드 안 모든 곡(기본·사진별)에 똑같이 넘기는 ACE-Step 파라미터.

    곡마다 달라지는 값(duration·caption_input)은 여기 두지 않는다.
    """

    bpm: int
    keyscale: str
    timesignature: str
    instrumental: bool = True

    def to_dict(self) -> dict[str, object]:
        """응답·analysis.json의 "shared" 형식."""
        return asdict(self)


def decide_shared(*, avg_pace, weather_code, emotion_direction) -> SharedParams:
    """러닝 기록 + 회고 분석 결과 → 카드 공통 설정.

    emotion_direction은 회고 분석(②) 결과라서, 이 함수는 반드시 회고 분석 뒤에 부른다.
    """
    return SharedParams(
        bpm=bpm_from_pace(avg_pace),
        keyscale=keyscale_for(emotion_direction, weather_group(weather_code)),
        timesignature=rules.TIME_SIGNATURE,
    )

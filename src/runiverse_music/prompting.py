"""곡별 caption_input 조립. 정본: docs/AI_음악_전처리_모듈.md 6장.

BPM·조성·박자·길이·장르·악기는 넣지 않는다 — 수치는 별도 파라미터로 넘긴다.
"""

from typing import Callable

from runiverse_music import mapping_rules as rules


def build_caption_input(activity: str, cue: str | None) -> str:
    """기본 음악: 활동 맥락 + 회고 단서(music_cue_en). cue가 없으면 활동 맥락만 쓴다."""
    head = f"An instrumental piece reflecting {rules.ACTIVITY_CONTEXT[activity]}"
    return _fit(cue, lambda text: f"{head} and {text}.", f"{head}.")


def build_photo_caption(activity: str, cue: str | None) -> str:
    """사진 음악: 사진 단서를 맨 앞에 두어 곡끼리 구분되게 한다. 활동 맥락은 뒤에 짧게 유지.

    cue가 없으면(사진 분석 실패) 기본 음악과 같은 활동 맥락 문장을 쓴다.
    """
    tail = f", an instrumental piece from {rules.ACTIVITY_CONTEXT[activity]}."
    return _fit(cue, lambda text: f"{text[0].upper()}{text[1:]}{tail}", build_caption_input(activity, None))


def _fit(cue: str | None, render: Callable[[str], str], fallback: str) -> str:
    """512자를 넘으면 cue를 뒤에서부터 단어 단위로 줄인다 — 활동 맥락은 항상 보존."""
    words = (cue or "").strip().rstrip(".").split()
    while words:
        caption = render(" ".join(words))
        if len(caption) <= rules.CAPTION_MAX_CHARS:
            return caption
        words.pop()
    return fallback

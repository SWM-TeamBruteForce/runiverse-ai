from runiverse_music import mapping_rules
from runiverse_music.prompting import build_caption_input, build_photo_caption


def test_base_caption_matches_doc_example():
    caption = build_caption_input("TRAVEL", "quiet pride after a difficult finish")
    assert caption == (
        "An instrumental piece reflecting a run while traveling and quiet pride after a difficult finish."
    )


def test_photo_caption_leads_with_photo_cue():
    # 곡끼리 구분되도록 사진 단서를 맨 앞에, 활동 맥락은 뒤에 짧게
    caption = build_photo_caption("TRAVEL", "an open, airy coastal scene")
    assert caption == "An open, airy coastal scene, an instrumental piece from a run while traveling."


def test_photo_captions_differ_from_the_first_word():
    first = build_photo_caption("TRAVEL", "a misty forest trail")
    second = build_photo_caption("TRAVEL", "neon city lights at night")
    assert first.split()[0:2] != second.split()[0:2]


def test_photo_caption_without_cue_keeps_activity_context_only():
    assert build_photo_caption("DAILY", None) == "An instrumental piece reflecting an everyday run."


def test_long_photo_cue_is_trimmed_and_keeps_activity_context():
    caption = build_photo_caption("TRAVEL", "word " * 200)
    assert len(caption) <= mapping_rules.CAPTION_MAX_CHARS
    assert caption.startswith("Word word")
    assert caption.endswith("word, an instrumental piece from a run while traveling.")


def test_missing_cue_keeps_activity_context_only():
    # 사진 분석 실패 → 사진 단서 없이 활동 맥락만으로 생성
    assert build_caption_input("DAILY", None) == "An instrumental piece reflecting an everyday run."


def test_trailing_period_in_cue_is_not_doubled():
    assert build_caption_input("RACE", "a bright finish.").endswith("a bright finish.")


def test_long_cue_is_trimmed_by_words_within_limit():
    caption = build_caption_input("TRAVEL", "word " * 200)
    assert len(caption) <= mapping_rules.CAPTION_MAX_CHARS
    assert caption.startswith("An instrumental piece reflecting a run while traveling and word")
    assert caption.endswith("word.")

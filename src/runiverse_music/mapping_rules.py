"""전처리 규칙값 (v1). 로직 없이 값만 둔다 — 계산은 music_params.py.

정본: docs/AI_음악_전처리_모듈.md 4장 (Notion "Ace-step 메타데이터 (공통 파라미터)")
값을 바꾸면: MAPPING_VERSION 올리기 → tests/test_music_params.py 기대값 수정 → 이미지 재빌드
실험만 할 때: pytest는 monkeypatch, Colab은 generator에 bpm·keyscale·duration을 직접 전달
"""

# 결과 manifest에 기록해 "어떤 규칙표로 만든 음악인지" 추적한다
MAPPING_VERSION = "v1"

# ── 페이스 → BPM ─────────────────────────────────────────────
# (상한 초/km, BPM). 위에서부터 차례로 비교해 "상한 미만"인 첫 구간을 고른다
PACE_TO_BPM: tuple[tuple[int, int], ...] = (
    (270, 180),  # 4:30/km 미만
    (360, 170),  # 4:30 ~ 5:59/km
    (450, 160),  # 6:00 ~ 7:29/km
)
SLOWEST_BPM = 150  # 7:30/km 이상
DEFAULT_BPM = 160  # 페이스 결측·유효하지 않음

# ── WMO Code Table 4677 → 날씨 그룹 ──────────────────────────
# (그룹, ((시작, 끝), ...)). 시작·끝 모두 포함. 0~99를 빈칸·중복 없이 덮는다
WEATHER_RANGES: tuple[tuple[str, tuple[tuple[int, int], ...]], ...] = (
    ("DRY_OR_RECENT", ((0, 3), (14, 16), (20, 29))),
    ("HAZE_OR_FOG", ((4, 12), (30, 35), (40, 49))),
    ("RAIN", ((50, 67), (80, 82), (91, 92))),
    ("SNOW_OR_ICE", ((36, 39), (68, 79), (83, 90), (93, 94))),
    ("STORM_OR_WIND", ((13, 13), (17, 19), (95, 99))),
)
# 가정값: 문서상 "기본 날씨 그룹" 미정 → 현재 현상이 없는 그룹으로 둔다 (협의 후 변경)
DEFAULT_WEATHER_GROUP = "DRY_OR_RECENT"

# ── 회고 방향 × 날씨 그룹 → 조성(keyscale) ────────────────────
# 열 순서 = WEATHER_GROUPS. 행 = Qwen3-VL이 고르는 emotion_direction 7종
WEATHER_GROUPS = ("DRY_OR_RECENT", "HAZE_OR_FOG", "RAIN", "SNOW_OR_ICE", "STORM_OR_WIND")
KEYSCALE_TABLE: dict[str, tuple[str, str, str, str, str]] = {
    "평온·담담함": ("C Major", "F Major", "F Major", "Eb Major", "C Major"),
    "설렘·가벼운 즐거움": ("A Major", "A Major", "D Major", "Eb Major", "D Major"),
    "성취·환희": ("D Major", "D Major", "E Major", "Eb Major", "E Major"),
    "감사·따뜻한 위로": ("G Major", "F Major", "G Major", "Eb Major", "Eb Major"),
    "아쉬움·그리움": ("G Minor", "E Minor", "G Minor", "D Minor", "C Minor"),
    "고뇌·결연함": ("C Minor", "F# Minor", "C Minor", "D Minor", "C Minor"),
    "깊은 슬픔·상실감": ("A Minor", "E Minor", "D Minor", "F Minor", "F Minor"),
}
# analyzer가 Qwen3-VL 출력을 검증할 때도 이 목록을 쓴다
EMOTION_DIRECTIONS: tuple[str, ...] = tuple(KEYSCALE_TABLE)
DEFAULT_EMOTION_DIRECTION = "평온·담담함"  # 회고 해석 실패·판단 불가

# ── 박자·길이 ────────────────────────────────────────────────
TIME_SIGNATURE = "4"           # 4/4박자. ACE-Step은 분자만 문자열로 받는다
BASE_DURATION_SECONDS = 30.0   # 기본 음악
PHOTO_DURATION_SECONDS = 10.0  # 사진별 음악

# ── 회고 분석 실패 시 기본값 ─────────────────────────────────
DEFAULT_EMOTION_CUE = "담담하고 안정적인 분위기"
DEFAULT_MUSIC_CUE_EN = "a calm and steady mood"  # 가정값: 문서는 "담담한 분위기의 기본값"까지만 정함

# ── caption_input ────────────────────────────────────────────
# 활동유형 → 영어 활동 맥락. TRAVEL은 문서 예시, DAILY·RACE는 가정값
ACTIVITY_CONTEXT: dict[str, str] = {
    "DAILY": "an everyday run",
    "TRAVEL": "a run while traveling",
    "RACE": "a race-day run",
}
CAPTION_MAX_CHARS = 512

# ── 생성 seed ────────────────────────────────────────────────
# 카드 입력에 seed가 없어 고정값을 쓴다 (가정값). 사진 곡은 seed + (사진 순서 + 1)로 파생
DEFAULT_SEED = 42

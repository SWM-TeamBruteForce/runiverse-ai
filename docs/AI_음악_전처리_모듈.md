# 🎛️ AI 음악 전처리 모듈

> 러닝 기록에서 카드 공통 **BPM·조성·박자**를 정하고, Qwen3-VL이 뽑은 분위기 단서로 곡별 **짧은 영어 입력**(`caption_input`)을 만드는 단계.
> 수치 설정은 ACE-Step의 별도 파라미터로만 넘긴다. 5Hz LM은 caption 개선에만 쓰고, 오디오 코드 생성(`thinking`)은 끈다.

- 원본: Notion [Ace-Step 전처리 모듈](https://app.notion.com/p/407ae181f7108076a2a5eefbdee681bd), [Ace-step 메타데이터 (공통 파라미터)](https://app.notion.com/p/407ae181f7108079b2a8d860ecf6d6d9)
- 기능명세: `docs/AI_러닝_음악_기능명세.md` (`AI-MUS-02` ~ `06`)
- 동기화: 2026-11-11 백엔드 협의 요청 JSON 반영
- 상태: 입력 필드명은 백엔드와 협의 완료 (2026-11-11)

---

## 📥 1. 입력

백엔드가 조회·구성한 기록카드 정보. 식별자는 결과 연결·저장 경로에, 회고·러닝 지표·사진은 생성 파라미터 변환에 쓴다.

| 필드 | 용도 | 규칙 |
|---|---|---|
| `user_id` | 사용자별 음악 저장 경로 구성 | 필수 |
| `record_card_id` | 결과와 기록카드 연결, 저장 경로 구성 | 필수 |
| `memo` | 회고 감정·여운 해석 | 미작성 시 `null` |
| `activity_type` | 카드 전체의 활동유형 | 대문자 코드 (`DAILY` / `TRAVEL` / `RACE`) |
| `running_summary.avg_pace` | BPM 결정 | 초/km (`5:20` → `320`). 없거나 잘못되면 대체 규칙 |
| `running_summary.weather_code` | 조성(`keyscale`) 선택 | WMO Code Table 4677 `ww` 코드 (0~99) |
| `running_summary`의 나머지 지표 | 러닝 맥락 보조 입력 | 현재 매핑에 쓰지 않음 (받기만 함) |
| `photos[].image_key` | 분석할 원본 사진의 S3 객체 키 | 필수 |
| `photos[].caption` | 사진별 사용자 설명 해석 | 미작성 시 `null` |

- 사진에는 별도 ID가 없다. **`photos` 배열 순서(0부터)** 로 사진과 음악 결과를 연결한다

```json
{
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
    "total_elevation_gain": 22.0
  },
  "photos": [
    {
      "image_key": "cards/101/photos/201.jpg",
      "caption": "바닷가에서 찍은 사진"
    }
  ]
}
```

## 📤 2. 출력

**카드 공통 설정 1개 + 회고 분석 + 곡별 생성 작업 목록.**

```json
{
  "record_card_id": 101,
  "mapping_version": "v1",
  "shared": {
    "bpm": 160,
    "keyscale": "E Major",
    "timesignature": "4",
    "instrumental": true
  },
  "review_analysis": {
    "emotion_direction": "성취·환희",
    "emotion_cue": "지친 끝에 찾아온 조용한 성취감",
    "music_cue_en": "quiet pride after a difficult finish"
  },
  "tracks": [
    {
      "type": "BASE",
      "photo_index": null,
      "duration": 30.0,
      "caption_input": "An instrumental piece reflecting a run while traveling and quiet pride after a difficult finish."
    },
    {
      "type": "PHOTO",
      "photo_index": 0,
      "duration": 10.0,
      "caption_input": "An instrumental piece reflecting a run while traveling and an open, airy coastal scene."
    }
  ]
}
```

- `shared`는 **모든 track에 똑같이** 적용 → 한 번의 러닝이라는 연결감
- track마다 다른 것은 `duration`과 `caption_input`뿐
- `photo_index`: 요청 `photos` 배열 순서. 사진 곡 seed도 이 순서로 파생 (`DEFAULT_SEED + photo_index + 1`)
- 생성 음악 저장 키 (AI 버킷): `{OUTPUT_PREFIX}/{user_id}/{activity_type}/{record_card_id}/base.wav`, 사진 곡은 `.../photos/{photo_index}.wav`
  - test: `OUTPUT_PREFIX=audio/test` / prod: `OUTPUT_PREFIX=audio`
  - 같은 카드를 다시 요청하면 같은 키를 덮어쓴다
- `caption_input`에는 활동 맥락과 곡별 분위기만. BPM·조성·박자는 문장에 반복하지 않는다
- `mapping_version`: 아래 규칙표의 버전. 규칙을 바꾸면 올리고 manifest에 기록

## 🔀 3. 처리 순서

| 단계 | 처리 | 결과 |
|---|---|---|
| ① 입력 확인 | 페이스 단위, 날씨 코드, 활동유형, 사진 ID·위치·캡션 확인 | 정리된 카드 입력 |
| ② 회고 분석 | Qwen3-VL에 회고 글 + 활동유형 전달 | `emotion_direction`, `emotion_cue`, `music_cue_en` |
| ③ 공통 수치 결정 | 평균 페이스 → BPM, 감정 방향 × 날씨 그룹 → 조성, 박자 고정 | 카드 공통 BPM·조성·박자 (`shared`) |
| ④ 사진 분석 | 사진마다 Qwen3-VL에 사진 + 사용자 캡션 전달, 활동유형은 맥락으로 제공 | 사진별 장면·분위기·영어 음악 단서 |
| ⑤ 곡별 입력 구성 | 활동 맥락 + (기본: 회고 여운 / 사진: 장면·분위기) | 곡별 `caption_input` |
| ⑥ 생성 작업 구성 | 공통 수치를 각 곡 파라미터로 지정, 길이 설정 | 기본 음악 1건 + 사진별 음악 각 1건 |
| ⑦ ACE-Step 생성 | 5Hz LM으로 caption 개선 → DiT로 음악 생성 | 기본 30초 + 사진별 10초 |

- 사진이 없으면 ④를 건너뛰고 기본 음악만 생성한다
- ③은 ②의 `emotion_direction`이 있어야 한다 → **회고 분석이 공통 수치보다 먼저**
- 사진 분석 결과로 회고 감정 방향이나 공통 수치를 **다시 계산하지 않는다**

## 🎚️ 4. 수치 파라미터 규칙

**박자·길이**

| 파라미터 | 기본 음악 | 사진 음악 |
|---|---|---|
| `timesignature` | `"4"` (4/4) | 동일 |
| `duration` | **30.0초** | **10.0초** |
| `instrumental` | `true` | `true` |

### BPM: 평균 페이스 기준

| `avg_pace` (초/km) | 표시용 페이스 | BPM | 리듬 |
|---|---|---|---|
| `< 270` | 4:30/km 미만 | 180 | 빠르고 추진력 있는 박자 |
| `270 ≤ x < 360` | 4:30~5:59/km | 170 | 경쾌하고 일정한 박자 |
| `360 ≤ x < 450` | 6:00~7:29/km | 160 | 편안하지만 움직임이 있는 박자 |
| `≥ 450` | 7:30/km 이상 | 150 | 여유롭고 안정적인 박자 |
| 결측·유효하지 않음 | 확인 불가 | **160** | — |

- 확정값은 ACE-Step `bpm` 파라미터로 전달. 회고 분위기는 BPM을 바꾸지 않는다

### 날씨 그룹 (WMO Code Table 4677)

| 그룹 | `ww` 코드 | 의미 |
|---|---|---|
| `DRY_OR_RECENT` | `00~03`, `14~16`, `20~29` | 현재 현상 없음·주변 현상·최근에 끝난 현상. **맑음으로 단정하지 않음** |
| `HAZE_OR_FOG` | `04~12`, `30~35`, `40~49` | 연기·먼지·모래폭풍·안개 등 흐릿한 대기 |
| `RAIN` | `50~67`, `80~82`, `91~92` | 현재 이슬비·비·비 소나기 |
| `SNOW_OR_ICE` | `36~39`, `68~79`, `83~90`, `93~94` | 날리는 눈·눈·진눈깨비·우박 등 |
| `STORM_OR_WIND` | `13`, `17~19`, `95~99` | 번개·돌풍·뇌우 관련. **모두 현재 강수를 뜻하지는 않음** |

### Keyscale: 회고 방향 × 날씨 그룹

| 회고 방향 | `DRY_OR_RECENT` | `HAZE_OR_FOG` | `RAIN` | `SNOW_OR_ICE` | `STORM_OR_WIND` |
|---|---|---|---|---|---|
| **평온·담담함** | `C Major` | `F Major` | `F Major` | `Eb Major` | `C Major` |
| **설렘·가벼운 즐거움** | `A Major` | `A Major` | `D Major` | `Eb Major` | `D Major` |
| **성취·환희** | `D Major` | `D Major` | `E Major` | `Eb Major` | `E Major` |
| **감사·따뜻한 위로** | `G Major` | `F Major` | `G Major` | `Eb Major` | `Eb Major` |
| **아쉬움·그리움** | `G Minor` | `E Minor` | `G Minor` | `D Minor` | `C Minor` |
| **고뇌·결연함** | `C Minor` | `F# Minor` | `C Minor` | `D Minor` | `C Minor` |
| **깊은 슬픔·상실감** | `A Minor` | `E Minor` | `D Minor` | `F Minor` | `F Minor` |

- 확정값은 ACE-Step `keyscale` 파라미터로 전달
- 날씨 코드가 없으면 **기본 날씨 그룹**을 쓰되 실제 날씨를 지어내지 않는다. 기본 그룹이 어느 것인지는 **미정** (협의 필요)

## 💬 5. 회고 분석 (Qwen3-VL 출력)

```json
{
  "emotion_direction": "성취·환희",
  "emotion_cue": "지친 끝에 찾아온 조용한 성취감",
  "music_cue_en": "quiet pride after a difficult finish"
}
```

| 필드 | 역할 |
|---|---|
| `emotion_direction` | **조성표 조회용 내부 분류값**. 위 표의 7개 방향 중 하나 |
| `emotion_cue` | 회고 글에 드러난 감정과 여운 보존 (한글 한 구절) |
| `music_cue_en` | 기본 음악 입력에 쓸 짧은 영어 단서 |

- 감정은 **회고 글을 근거로** 판단. 페이스·날씨만으로 추정하지 않는다
- 해석 실패 → `emotion_direction=평온·담담함`, 음악 단서는 담담한 분위기의 기본값

## 🧩 6. Caption 입력 구성

순서: **활동 맥락 → 곡별 분위기 단서 → 길이·내용 확인**. 공통 수치, 고정 장르, 공통 악기 구성은 문장에 넣지 않는다.

| 구성 요소 | 기본 음악 | 사진 음악 |
|---|---|---|
| 활동유형 | 카드의 러닝 맥락 반영 | 동일한 카드 활동 맥락 반영 |
| 회고 분위기 | Qwen3-VL의 `music_cue_en` 반영 | caption에 **반복해서 넣지 않음** |
| 사진 장면·분위기 | 사용하지 않음 | Qwen3-VL이 추출한 사진 단서 반영 |
| 사용자 사진 캡션 | 사용하지 않음 | 사진의 맥락을 확인하는 데 사용 |
| 장르·악기 | 미리 지정하지 않음 | 미리 지정하지 않음 |
| 연주곡 조건 | `instrumental=true`, `lyrics="[Instrumental]"` | 동일 |

```txt
활동 맥락 + 회고 글의 음악적 여운
→ 기본 음악 caption_input

활동 맥락 + 사진 장면·분위기 + 사용자 사진 캡션의 핵심
→ 해당 사진 음악 caption_input
```

- Qwen3-VL은 완성된 caption이나 장르를 쓰지 않는다. 한국어 회고·사진 캡션을 이해하고 **짧은 영어 음악 단서**만 출력한다. 별도 번역 모델 없음
- `bpm`·`keyscale`·`timesignature`·`duration`은 caption에 쓰지 않고 **별도 파라미터**로 전달
- **512자 이내** 검증. 줄여야 하면 부수적인 장면 설명부터 압축, **활동 맥락·곡별 핵심 분위기**는 보존

### 예시

> 페이스 `6:00/km`(360초), 활동 `TRAVEL`, 회고 "힘들었지만 끝까지 달려서 뿌듯했다", 날씨 `ww=61`(`RAIN`)
> → **160 BPM · E Major(성취·환희 × RAIN) · 4/4** · 기본 30초 / 사진 10초

```txt
기본 음악 caption_input — 30초:
An instrumental piece reflecting a run while traveling and
quiet pride after a difficult finish.

바다 사진 caption_input — 10초:
An instrumental piece reflecting a run while traveling and
an open, airy coastal scene.
```

## 🤖 7. ACE-Step 5Hz LM 적용

별도 `format_sample` 호출 없이 생성 과정에서 아래 옵션을 적용한다.

| 옵션 | 1차 설정 | 목적 |
|---|---|---|
| `use_cot_caption` | `true` | 5Hz LM이 짧은 입력을 음악 생성용 caption으로 개선 |
| `thinking` | `false` | 5Hz LM의 오디오 코드 생성 단계는 쓰지 않음 |
| `use_cot_metas` | `false` | 공통 수치의 추가 추론 최소화 |
| `instrumental` | `true` | 연주곡 생성 |
| `lyrics` | `"[Instrumental]"` | 가사 없는 곡임을 명시 |

- `thinking=false`여도 `use_cot_caption=true`면 5Hz LM은 caption 개선에 쓰인다 ([ACE-Step `inference.py`](https://github.com/ace-step/ACE-Step-1.5/blob/main/acestep/inference.py))
- BPM·조성·박자·길이는 각 곡의 생성 파라미터에 명시
- PoC 기록: 입력 문장 · 5Hz LM이 개선한 caption · 실제 음원 → 사진별 분위기가 살아 있는지, 카드 안 곡들이 서로 어울리는지 확인

## ⚠️ 8. 원본에서 확인 필요

| 항목 | 내용 |
|---|---|
| 사진 순서 안정성 | 사진 ID가 없어 배열 순서로 연결 → 재요청 사이에 사진을 지우거나 순서를 바꾸면 `photos/{순서}.wav`가 다른 사진을 가리키고, 사진 수가 줄면 이전 파일이 남는다 |
| 기본 날씨 그룹 | 날씨 결측 시 쓸 그룹 미지정 |
| `activity` 코드 목록 | 원본에는 "대문자 코드"만 있음. `DAILY`/`TRAVEL`/`RACE`는 이전 개정본 기준 |

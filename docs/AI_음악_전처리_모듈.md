# 🎛️ AI 음악 전처리 모듈

> 카드 입력(러닝 기록·회고 글·날씨·사진)을 **ACE-Step 생성 요청 목록**으로 바꾸는 단계.
> 카드마다 BPM·조성·박자를 **한 번** 정해 모든 곡에 공통 적용하고, 곡마다 길이와 caption만 다르게 만든다.

- 원본: Notion [Ace-Step 전처리 모듈](https://app.notion.com/p/407ae181f7108076a2a5eefbdee681bd), [Ace-step 메타데이터 (공통 파라미터)](https://app.notion.com/p/407ae181f7108079b2a8d860ecf6d6d9)
- 기능명세: `docs/AI_러닝_음악_기능명세.md` (`AI-MUS-02` ~ `06`)
- 동기화: 2026-10-28 Notion 개정본 기준
- 상태: 필드명은 백엔드·AI 데이터 계약에서 최종 확정

---

## 📥 1. 입력

| 필드 | 용도 | 규칙 |
|---|---|---|
| `card_id` | 결과와 기록 카드 연결 | 필수 |
| `runningRecordId` | 러닝 기록 연결 | 필수 |
| `activity` | 카드 전체의 활동유형 | `DAILY`(일상) / `TRAVEL`(여행) / `RACE`(대회) |
| `averagePaceSecondsPerKm` | BPM 결정 | 초/km 정수 (`5:20` → `320`). 없거나 잘못되면 대체 규칙 |
| `review_text` | 회고 감정·여운 해석 | 비어 있으면 기본 분위기 사용 |
| `weather_code` | 조성(`keyscale`) 선택 | WMO Code Table 4677 `ww` 코드 (0~99) |
| `photos[]` | 사진별 음악 | `photo_id`, `image_s3_key`, `user_caption`(선택) |

```json
{
  "card_id": "card-001",
  "runningRecordId": "run-001",
  "activity": "TRAVEL",
  "averagePaceSecondsPerKm": 320,
  "review_text": "힘들었지만 끝까지 달려서 뿌듯했다",
  "weather_code": 61,
  "photos": [
    {
      "photo_id": "photo-001",
      "image_s3_key": "cards/card-001/photos/photo-001.jpg",
      "user_caption": "바닷가에서 찍은 사진"
    }
  ]
}
```

## 📤 2. 출력

```json
{
  "card_id": "card-001",
  "mapping_version": "v1",
  "shared": {
    "bpm": 170,
    "keyscale": "E Major",
    "timesignature": "4",
    "instrumental": true
  },
  "tracks": [
    { "type": "BASE",  "photo_id": null,        "duration": 30.0, "caption": "..." },
    { "type": "PHOTO", "photo_id": "photo-001", "duration": 10.0, "caption": "..." }
  ]
}
```

- `shared`는 **모든 track에 똑같이** 적용 → 한 번의 러닝이라는 연결감
- track마다 다른 것은 `duration`과 `caption`뿐
- `mapping_version`: 아래 규칙표의 버전. 규칙을 바꾸면 올리고 manifest에 기록

## 🔀 3. 처리 순서

| 단계 | 처리 | 결과 |
|---|---|---|
| ① 입력 확인 | 페이스 단위, 날씨 코드, 활동유형, 사진 ID·캡션 확인 | 정리된 카드 입력 |
| ② 회고 해석 | Qwen3-VL에 회고 글만 전달 | `emotion_cue`, `emotion_direction`, `music_emotion_en` |
| ③ 공통 설정 | 페이스 → BPM, 회고 방향 × 날씨 그룹 → 조성 | 공통 BPM·조성·박자 (`shared`) |
| ④ 사진 분석 | 사진마다 Qwen3-VL로 장면·분위기·음악 단서 추출 | 사진별 분석 결과 |
| ⑤ caption 조립 | 공통 스타일 + 활동 맥락 + 회고 여운 (+ 사진 단서) | 곡별 **영어** caption |
| ⑥ 생성 작업 구성 | 기본 음악 30초, 사진 음악 각 10초 | 기본 음악 1개 + 사진별 음악 N개 |

- 사진이 없으면 ④를 건너뛰고 기본 음악만 생성한다
- ③은 ②의 `emotion_direction`이 있어야 한다 → **회고 해석이 공통 설정보다 먼저**

## 🎚️ 4. 공통 설정 규칙

**공통 기준**

| 파라미터 | 값 |
|---|---|
| `timesignature` | `"4"` (4/4) |
| `instrumental` | `true` |
| `duration` | 기본 음악 **30초** / 사진 음악 **10초** |

### BPM: 평균 페이스 기준

| `averagePaceSecondsPerKm` | 표시용 페이스 | BPM | 리듬 |
|---|---|---|---|
| `< 270` | 4:30/km 미만 | 180 | 빠르고 추진력 있는 박자 |
| `270 ≤ x < 360` | 4:30~5:59/km | 170 | 경쾌하고 일정한 박자 |
| `360 ≤ x < 450` | 6:00~7:29/km | 160 | 편안하지만 움직임이 있는 박자 |
| `≥ 450` | 7:30/km 이상 | 150 | 여유롭고 안정적인 박자 |
| 결측·유효하지 않음 | 확인 불가 | **160** | — |

- 회고 분위기는 BPM을 바꾸지 않는다

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

- 날씨 코드가 없거나 범위 밖일 때 쓸 그룹은 **미정** (협의 필요)

## 💬 5. 회고 해석 (Qwen3-VL 출력)

```json
{
  "emotion_cue": "지친 끝에 찾아온 조용한 성취감",
  "emotion_direction": "성취·환희",
  "music_emotion_en": "quiet pride after a difficult run"
}
```

| 필드 | 용도 | 규칙 |
|---|---|---|
| `emotion_cue` | 생생한 회고 표현 보관 | 한글 한 구절, 30자 이내 |
| `emotion_direction` | **조성 선택용 내부 값** | 위 표의 7개 방향 중 하나 |
| `music_emotion_en` | caption에 넣을 영어 표현 | 영어 짧은 구 |

- 해석 실패·감정 불명확 → `emotion_direction=평온·담담함`, `emotion_cue=담담하고 안정적인 분위기`
- 글에 없는 감정을 러닝 수치·사진으로 만들어내지 않는다

## 🧩 6. Caption 조립

순서: **공통 문장 → 곡별 단서 → 출력 검증**

| 구성 요소 | 기본 음악 | 사진 음악 |
|---|---|---|
| 공통 스타일 | 예: `electronic pop`, 일정한 드럼, 신스 중심 | **동일하게 유지** |
| 활동유형 | 카드 전체의 활동 맥락 반영 | 동일한 활동 맥락 반영 |
| 회고 분위기 | `music_emotion_en`을 중심에 배치 | 같은 감정 방향을 짧게 유지 |
| 날씨 | 실제 날씨를 알 때만 질감에 반영 | 사진에 보이지 않는 날씨를 장면으로 단정하지 않음 |
| 사진 장면·분위기 | 사용하지 않음 | Qwen3-VL의 장면·음악 단서 반영 |
| 사용자 사진 캡션 | 사용하지 않음 | 사진의 맥락으로 반영. 긴 원문은 음악에 필요한 의미로 요약 |
| 금지·제약 | `Instrumental, no vocals.` | 동일 |

```txt
공통 스타일 + 활동 맥락 + 회고의 음악적 여운
→ 기본 음악 caption

공통 스타일 + 활동 맥락 + 회고의 음악적 여운
+ 사진 장면·분위기 + 사용자 사진 캡션의 핵심
→ 해당 사진 음악 caption
```

- caption은 **영어**
- `bpm`·`keyscale`·`timesignature`·`duration`은 caption에 쓰지 않고 **별도 파라미터**로 전달
- **512자 초과** → 사진 장면 설명부터 압축. 공통 스타일·회고 여운·연주곡 조건은 보존

### 예시

> 페이스 `5:20/km`(320초), 회고 "힘들었지만 끝까지 달려서 뿌듯했다", 날씨 `ww=61`(`RAIN`)
> → **170 BPM · E Major(성취·환희 × RAIN) · 4/4** · 기본 30초 / 사진 10초

```txt
기본 음악:
Electronic pop with a steady running beat and a consistent synth palette.
Quiet pride after a difficult run, with a gentle uplifting finish.
Instrumental, no vocals.

바다 사진 음악:
Electronic pop with a steady running beat and a consistent synth palette.
Quiet pride after a difficult run, with airy coastal textures and an open,
reflective atmosphere. Instrumental, no vocals.
```

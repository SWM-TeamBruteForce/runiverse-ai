# 🎵 Runiverse 음악 생성 — SageMaker dev 배포 설계

> Colab 베이스라인(ACE-Step 1.5 Turbo)을 AWS SageMaker dev 환경의 비동기 엔드포인트로 옮긴다.

- 작성일: 2026-10-14
- 베이스라인: `Runiverse_ACE_Step_1_5_Colab_Python311_CUDA.ipynb` (Google Drive)
- 상태: 설계 검토 중

---

## 🎯 1. 목표와 범위

**목표**
- 노트북의 `infer_music.py` 로직을 SageMaker 컨테이너로 이식
- 모델은 컨테이너 시작 시 1회 로드, 요청마다 재사용
- dev 엔드포인트에서 기본 요청(170 BPM · 30초 · seed 42 · 무보컬) 생성 성공

**성공 기준**
- `/ping` 200 → 엔드포인트 `InService`
- 스모크 요청 → S3에 wav(48kHz, 약 30초) + 응답 JSON 생성
- manifest에 코드·모델·환경 버전 기록

**범위 밖**
- 백엔드의 SNS 구독·상태 관리, 앱 UI
- prod 환경, CI/CD(GitHub Actions)
- LM(`use_lm`) 사용, 보컬 생성
- SageMaker 로그의 Loki 통합

---

## 📁 2. 저장소

| 항목 | 값 |
|---|---|
| 원격 | `SWM-TeamBruteForce/runiverse-ai` (미생성) |
| 로컬 | `C:\dev\runiverse\ai` |
| 범위 | 당분간 음악 생성 전용 → 코드는 루트에 직접 배치 |

```
C:\dev\runiverse\ai\
├─ README.md
├─ pyproject.toml                    # 서빙 코드 의존성 (ACE-Step은 컨테이너에서 고정)
├─ container/
│  ├─ Dockerfile
│  └─ serve                          # SageMaker 진입점
├─ src/runiverse_music/
│  ├─ config.py
│  ├─ schemas.py
│  ├─ generator.py
│  ├─ storage.py
│  └─ app.py
├─ scripts/
│  ├─ deploy_config.py               # envs/<env>.yaml 로더·검증 (스크립트 공용)
│  ├─ download_models.py             # HF → S3 model.tar.gz
│  ├─ build_and_push.sh              # 이미지 → ECR
│  ├─ deploy.py                      # --env dev
│  └─ smoke_test.py
├─ deploy/envs/dev.yaml
├─ notebooks/
│  ├─ baseline_colab.ipynb           # 기존 베이스라인 보관
│  └─ colab_verify.ipynb             # Colab GPU에서 서버 코드 실검증
├─ tests/
└─ docs/superpowers/specs/
```

---

## 🧱 3. 배포 방식

> ✅ **선택: BYOC 커스텀 컨테이너**

- Colab 검증 환경 그대로 재현: Python 3.11 · PyTorch 2.10 / CUDA 12.8 · ACE-Step `ca1e85f` · `uv.lock --frozen`
- 노트북 결론("8080 `/ping`·`/invocations`")과 일치

> ❌ **기각: AWS PyTorch DLC + `inference.py`**

- DLC의 Python·PyTorch 버전이 `uv.lock`과 불일치 가능성 높음
- 불일치 시 런타임 설치 → 콜드 스타트 증가, 재현성 저하

---

## 🔀 4. 아키텍처와 데이터 흐름

```
[Spring Boot 백엔드]
   │ ① 요청 JSON → s3://<bucket>/music/requests/{request_id}.json
   │ ② InvokeEndpointAsync(InputLocation)
   ▼
[SageMaker Async Endpoint: runiverse-music-dev]
   ├─ 컨테이너 시작: /opt/ml/model(← S3 model.tar.gz) 로드
   ├─ POST /invocations → 생성 → wav를 S3 outputs/ 업로드 → 응답 JSON
   ├─ 응답 JSON → s3://<bucket>/music/responses/
   └─ SNS 성공/실패 토픽 → 백엔드 → 앱에는 presigned URL
```

**S3 경로 규칙**

| 용도 | 경로 |
|---|---|
| 모델 | `s3://<bucket>/music/models/ace-step-1.5/{hf_revision}/model.tar.gz` |
| 요청 | `s3://<bucket>/music/requests/{request_id}.json` |
| 음원 | `s3://<bucket>/music/outputs/{request_id}/{request_id}.wav` |
| 응답 | `s3://<bucket>/music/responses/` (SageMaker 관리) |
| 실패 | `s3://<bucket>/music/failures/` (SageMaker 관리) |

**핵심 결정**
- 호출 방식: **Async** — 60초 응답 제한 없음, 인스턴스 0대 축소 가능
- 가중치: **S3 `model.tar.gz`** — 이미지 경량화, 코드·모델 버전 분리
- 호출 주체: **백엔드** — 앱에 AWS 자격 증명 두지 않음

---

## 🐳 5. 컨테이너 내부 구조

| 파일 | 역할 | 의존 |
|---|---|---|
| `container/Dockerfile` | CUDA 런타임 베이스 → uv + Python 3.11 → ACE-Step `ca1e85f` clone → `uv sync --frozen --no-dev` → 서빙 코드 설치 | — |
| `container/serve` | uvicorn `0.0.0.0:8080`, 워커 1개 (GPU 1장 = 모델 1벌) | `app` |
| `config.py` | 환경변수 로드·검증, 잘못된 값이면 시작 시 실패 | — |
| `schemas.py` | `MusicRequest` / `MusicResponse` (Pydantic, 범위 검증) | — |
| `generator.py` | `MusicGenerator.load(model_dir)` 1회 / `generate(req, out_dir)` 요청마다. **torch·acestep import는 이 파일만**. `FakeGenerator`: 모델 없이 짧은 무음 wav 반환 | `acestep` |
| `storage.py` | `build_key(request_id)`, `upload(path, key)`. `S3Storage` / `LocalStorage`(로컬 폴더 복사) | `boto3` |
| `app.py` | lifespan에서 `load()` → `/ping`은 로드 완료 후 200 → `/invocations`는 검증 → 생성 → 업로드 → 응답. GPU 작업은 lock으로 직렬화 | 전부 |

**가중치 배치**
- `/opt/ml/model` = 노트북의 `checkpoints/` 내용
- `ACESTEP_CHECKPOINTS_DIR=/opt/ml/model`, `project_root`는 ACE-Step clone 경로

**📥 요청 (`MusicRequest`)**

| 필드 | 타입 | 규칙 |
|---|---|---|
| `request_id` | str | 필수, 백엔드 발급, S3 키에 사용 |
| `caption` | str | 필수, 1~512자 |
| `bpm` | int | 30~300 |
| `duration` | float | 10~`MAX_DURATION_SECONDS` |
| `seed` | int | 필수 |
| `instrumental` | bool | dev는 `true`만 허용 |

**📤 응답 (`MusicResponse`)**
- `request_id`, `audio_s3_key`, `sample_rate`, `channels`, `actual_duration_seconds`, `bytes`
- `generation_seconds`, `peak_torch_allocated_gib`
- `manifest` (9장 항목)

---

## ⚙️ 6. 환경 설정 (`deploy/envs/dev.yaml`)

> `deploy.py` 실행 시 읽음 → **값 변경 후 재배포만 하면 반영, 이미지 재빌드 불필요**

**인프라 값**

| 키 | 기본값 |
|---|---|
| `region` | `ap-northeast-2` (서울) |
| `endpoint_name` | `runiverse-music-dev` |
| `instance_type` | `ml.g4dn.xlarge` (T4 16GB, Colab 검증 GPU와 동일) |
| `autoscaling` | min 0 / max 1 |
| `max_concurrent_invocations_per_instance` | 1 |
| `bucket` | 미정 |
| `execution_role_arn` | 미정 |
| `sns_success_topic` / `sns_error_topic` | 미정 |
| `model_data_download_timeout` / `container_startup_health_check_timeout` | 각 600초 |

**컨테이너 환경변수** (SageMaker Model `Environment`로 전달)

| 키 | 기본값 |
|---|---|
| `OUTPUT_BUCKET` / `OUTPUT_PREFIX` | `<bucket>` / `music/outputs` |
| `MAX_DURATION_SECONDS` | 120 |
| `INFERENCE_STEPS` | 8 |
| `OFFLOAD_TO_CPU` | `true` |
| `AUDIO_FORMAT` | `wav` |
| `LOG_LEVEL` | `INFO` |
| `GENERATOR` | `acestep` (테스트용 `fake`) |
| `STORAGE` | `s3` (테스트용 `local`) |
| `LOCAL_OUTPUT_DIR` | `STORAGE=local`일 때만 사용 |

> ⚠️ dev.yaml에서는 `GENERATOR=acestep`, `STORAGE=s3` 고정 — `fake`·`local`은 로컬·Colab 검증 전용. `deploy.py`는 다른 값이면 배포 거부

**재빌드가 필요한 값** → `dev.yaml`이 아닌 Dockerfile·`pyproject.toml`에서 관리
- ACE-Step 커밋, CUDA 베이스 이미지, Python 의존성

---

## 🚨 7. 오류 처리

| 상황 | 처리 |
|---|---|
| 모델 로드 실패 (가중치 없음, CUDA 없음) | `/ping` 실패 → 엔드포인트 `Failed`. **CPU 대체 금지** |
| 요청 범위 위반 | 400 → Async 실패 → SNS 실패 알림 |
| CUDA OOM | `empty_cache()` 후 500, 자동 재시도 없음, 서비스 유지 |
| 생성 성공이나 파일 없음·0바이트 | 500 |
| S3 업로드 실패 | boto3 기본 재시도 후 500 |

---

## 📊 8. 관측

**CloudWatch (자동)**
- 로그: stdout → `/aws/sagemaker/Endpoints/runiverse-music-dev`
- 지표: `Invocations`, `ModelLatency`, 4xx/5xx, GPU·메모리 사용률
- 로그 형식: 요청당 JSON 한 줄 — `request_id`, 로딩·생성 시간, 피크 VRAM, 결과
- `LOG_LEVEL=INFO` → ACE-Step DEBUG 로그 억제 (수집 비용 절감)

**스케일링 알람 (`deploy.py`가 생성)**
- `ApproximateBacklogSizePerInstance` 기반 타깃 추적
- `HasBacklogWithoutCapacity` 알람 → 0대에서 1대로 확장

**Grafana 연결** — 저장소 범위 밖, Grafana 쪽 설정
- 기존 호스팅 Grafana에 CloudWatch 데이터 소스 추가
- 자격 증명: EKS면 IRSA/Pod Identity, EC2면 인스턴스 Role
- IAM 읽기 권한: `cloudwatch:GetMetricData`, `ListMetrics`, `logs:StartQuery`, `GetQueryResults`, `DescribeLogGroups`
- ⚠️ Loki(LogQL)와 쿼리 통합 불가 → 같은 대시보드 패널로 병치, `request_id`로 대조
- ⚠️ 자동 새로고침 1~5분 권장 (조회량 과금)

**dev에서 제외**
- 커스텀 지표, 전용 대시보드, 장애 알람

---

## 🔁 9. 재현성

manifest에 기록:
- `source_commit` (ACE-Step)
- `model_revisions` (HF 커밋 sha)
- `uv_lock_sha256`
- `image_tag` (runiverse-ai git sha)
- `python`, `torch`, `torch_cuda`, `gpu`, `dtype`
- `model_loading_seconds`, 요청 설정

---

## 🧪 10. 테스트

> 로컬 PC에 GPU 없음 → ①② 로컬, ③ Colab GPU, ④ dev 엔드포인트 순으로 단계 검증

| 단계 | 위치 | 검증 대상 | GPU |
|---|---|---|---|
| ① 단위 테스트 | 로컬 `pytest` | 로직·검증·응답 코드 | ❌ |
| ② 컨테이너 계약 | 로컬 Docker | 이미지·`serve`·8080·SageMaker 계약 | ❌ |
| ③ 실생성 | Colab T4 | 실제 음원 생성 전체 코드 경로 | ✅ 무료 |
| ④ 스모크 | dev 엔드포인트 | 드라이버·콜드 스타트·Async·SNS | ✅ 과금 |

### ① 단위 테스트
- `schemas`: 범위 경계값
- `config`: 환경변수 파싱·오류, 알 수 없는 `GENERATOR`·`STORAGE` 값 거부
- `storage`: 키 규칙, `LocalStorage` 저장
- `app`: `FakeGenerator`·`LocalStorage`로 `/ping` 준비 전후, `/invocations` 200/400/500

### ② 컨테이너 계약 (Docker Desktop)
- `docker build` → `docker run -p 8080:8080 -e GENERATOR=fake -e STORAGE=local <image> serve`
- `curl /ping` 200, `/invocations` 기본 요청 → 응답 JSON 확인
- 잡는 문제: Dockerfile·진입점·포트·응답 형식 오류
- ⚠️ CUDA·PyTorch 포함 이미지 약 10GB → 첫 빌드 오래 걸림

### ③ Colab 실생성 (`notebooks/colab_verify.ipynb`)
1. 베이스라인 셀로 ACE-Step 환경·모델 준비
2. runiverse-ai 코드를 같은 venv에 설치
   - 원격 저장소 전: Drive mount 후 설치
   - 원격 저장소 후: `pip install git+https://...` (비공개면 토큰)
3. checkpoints를 `/opt/ml/model` 구조로 연결
4. 백그라운드 `uvicorn runiverse_music.app` → `localhost:8080`
5. `requests`로 `/ping`·`/invocations` 호출 → 응답 JSON 확인, wav 재생
- `GENERATOR=acestep`, `STORAGE=local` (Colab에 AWS 키 미보관)
- 미검증: Docker 이미지, SageMaker 드라이버, Async 큐, SNS

### ④ dev 스모크 (`scripts/smoke_test.py`)
- 기본 요청 Async 호출 → 응답 JSON 확인
- wav 존재, 48kHz, 길이 약 30초 확인
- ①~③ 통과 후 진행 (엔드포인트 수정 1회에 10분 이상 소요)

> 💡 ④에서 반복 실패 시: SageMaker Notebook Instance(GPU) local mode로 컨테이너 직접 실행 — 시간당 과금이라 필요할 때만

---

## ❓ 11. 가정과 미결 사항

**확정**
- 리전 = 서울 `ap-northeast-2` (S3·ECR·SageMaker·SNS·CloudWatch 모두 동일 리전)

**가정**
- 호출 주체 = Spring Boot 백엔드
- dev 최대 길이 = 120초

**미결 (실제 AWS 리소스 필요)**
- S3 버킷, 실행 IAM Role, SNS 토픽, ECR 저장소 이름
- GitHub 원격 저장소 생성

**⚠️ 검증 필요 리스크**
- SageMaker GPU 호스트 드라이버 ↔ PyTorch CUDA 12.8 호환 → 필요 시 ProductionVariant `InferenceAmiVersion` 지정
- `model.tar.gz` 압축 해제로 콜드 스타트 증가 → 느리면 비압축 S3 prefix(`CompressionType: None`) 전환 검토

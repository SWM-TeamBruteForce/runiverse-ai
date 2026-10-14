# 🎵 runiverse-ai

Runiverse 음악 생성(ACE-Step 1.5 Turbo) — SageMaker Async 엔드포인트 서빙.

- 설계: `docs/superpowers/specs/2026-10-14-sagemaker-music-gen-design.md`
- 구현 계획: `docs/superpowers/plans/2026-10-14-sagemaker-music-gen.md`

## 📁 구조

| 경로 | 내용 |
|---|---|
| `src/runiverse_music/` | 서빙 코드 (`/ping`, `/invocations`) |
| `container/` | Dockerfile, `serve` 진입점 |
| `scripts/` | 모델 패키징·이미지 푸시·배포·스모크 |
| `deploy/envs/dev.yaml` | dev 배포 설정 (재배포만으로 반영) |
| `notebooks/` | 베이스라인, Colab 검증 |

## 🛠️ 로컬 준비

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev,deploy]"
```

> 💡 Git Bash에서 스크립트 한글 출력이 깨지면 `export PYTHONUTF8=1`

## 🧪 검증 단계

| 단계 | 명령 |
|---|---|
| ① 단위 테스트 | `.venv/Scripts/python -m pytest` |
| ② 컨테이너 계약 | `docker build -f container/Dockerfile -t runiverse-music:local .` → `docker run -p 8080:8080 -e GENERATOR=fake -e STORAGE=local -e LOCAL_OUTPUT_DIR=/tmp/out runiverse-music:local serve` |
| ③ Colab 실생성 | `notebooks/colab_verify.ipynb` |
| ④ dev 스모크 | `python scripts/smoke_test.py --env dev` |

## 🚀 dev 배포 (서울 `ap-northeast-2`)

> 사전: AWS CLI 로그인, Docker Desktop, venv 활성화(`source .venv/Scripts/activate`)

1. `deploy/envs/dev.yaml`에 `bucket`·`execution_role_arn`·(선택) SNS 토픽 입력
2. 가중치: `python scripts/download_models.py --env dev` → 출력 URI를 `model_data_url`에 입력
3. 이미지: `scripts/build_and_push.sh dev` → 마지막 줄 이미지 URI 복사
4. 미리보기: `python scripts/deploy.py --env dev --image-uri <URI> --dry-run`
5. 배포: `python scripts/deploy.py --env dev --image-uri <URI>`
6. 확인: `python scripts/smoke_test.py --env dev`

- 설정만 변경: `dev.yaml` 수정 → 5번만 재실행 (같은 이미지 URI)
- 코드 변경: 커밋 → 3·5번

## 📊 관측

- 로그: CloudWatch `/aws/sagemaker/Endpoints/runiverse-music-dev` (요청당 JSON 한 줄, `request_id`로 검색)
- 지표: `AWS/SageMaker` — `Invocations`, `ModelLatency`, `ApproximateBacklogSizePerInstance`
- Grafana: 기존 Grafana에 CloudWatch 데이터 소스 추가 (기본 리전 서울), 새로고침 1~5분

## ⚠️ 알려진 리스크

- GPU 호스트 드라이버 ↔ CUDA 12.8 불일치 시 → `dev.yaml`의 `inference_ami_version` 지정 후 재배포
- 0대에서 첫 요청 → 콜드 스타트 10분 이상 가능

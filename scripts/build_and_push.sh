#!/usr/bin/env bash
# 이미지 빌드 → ECR push. 태그 = 현재 git 커밋.
# 사용: scripts/build_and_push.sh [env]   (venv 활성화 상태, AWS CLI 로그인 상태)
set -euo pipefail

ENV_NAME="${1:-dev}"
PY="${PYTHON:-python}"
cd "$(dirname "$0")/.."

if [ -n "$(git status --porcelain)" ]; then
  echo "커밋되지 않은 변경이 있습니다. 태그와 코드가 어긋나므로 커밋 후 실행하세요." >&2
  exit 1
fi

REGION="$("$PY" scripts/deploy_config.py "$ENV_NAME" region)"
REPO="$("$PY" scripts/deploy_config.py "$ENV_NAME" ecr_repository)"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
TAG="$(git rev-parse --short=12 HEAD)"
REGISTRY="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
IMAGE="$REGISTRY/$REPO:$TAG"

aws ecr describe-repositories --repository-names "$REPO" --region "$REGION" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "$REPO" --region "$REGION" >/dev/null
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"

# SageMaker Model은 Docker v2 매니페스트만 받는다. Docker Desktop(containerd 저장소)의 build·push는 OCI 형식이라 거부됨
#   --provenance·--sbom=false: 출처 정보가 붙은 OCI 이미지 인덱스(oci.image.index) 방지
#   oci-mediatypes=false:      단일 이미지도 OCI 매니페스트(oci.image.manifest) 대신 Docker v2로 푸시
# ECR 태그가 immutable이라 같은 태그가 이미 있으면 push가 실패한다 → Console에서 기존 이미지 삭제 후 재실행
docker buildx build --platform linux/amd64 --provenance=false --sbom=false \
  -f container/Dockerfile --build-arg IMAGE_TAG="$TAG" \
  --output "type=registry,name=$IMAGE,oci-mediatypes=false" .
# MediaType이 application/vnd.docker.distribution.manifest.v2+json 이어야 Model 생성 가능
docker buildx imagetools inspect "$IMAGE" | head -5
echo "$IMAGE"

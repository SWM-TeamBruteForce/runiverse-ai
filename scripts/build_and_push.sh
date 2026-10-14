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

docker build --platform linux/amd64 -f container/Dockerfile --build-arg IMAGE_TAG="$TAG" -t "$IMAGE" .
docker push "$IMAGE"
echo "$IMAGE"

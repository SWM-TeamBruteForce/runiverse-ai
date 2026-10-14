# Runiverse 음악 생성 — SageMaker BYOC 컨테이너
# PyTorch cu128 휠이 CUDA 라이브러리를 포함 → base 이미지로 충분
FROM nvidia/cuda:12.8.1-base-ubuntu22.04

ARG ACESTEP_COMMIT=ca1e85fe9430179831e6bc6be790c332190a3866
ARG ACESTEP_REPO=https://github.com/ace-step/ACE-Step-1.5.git
ARG IMAGE_TAG=local

ENV DEBIAN_FRONTEND=noninteractive \
    UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates ffmpeg libsndfile1 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.8.4 /uv /uvx /usr/local/bin/

# ACE-Step 고정 커밋 + 공식 uv.lock 그대로 설치 (Colab 검증 환경 재현)
WORKDIR /opt/ace-step
RUN git init -q . \
 && git remote add origin "$ACESTEP_REPO" \
 && git fetch -q --depth 1 origin "$ACESTEP_COMMIT" \
 && git checkout -q --detach FETCH_HEAD \
 && uv python install 3.11 \
 && uv sync --frozen --no-dev --python 3.11 \
 && rm -rf checkpoints \
 && ln -s /opt/ml/model checkpoints

ENV PATH=/opt/ace-step/.venv/bin:$PATH

# 서빙 코드는 ACE-Step venv에 설치 (이미 있는 의존성 버전 우선)
COPY pyproject.toml README.md /opt/app/
COPY src /opt/app/src
RUN uv pip install --python /opt/ace-step/.venv/bin/python --no-cache /opt/app

COPY container/serve /usr/local/bin/serve
RUN chmod +x /usr/local/bin/serve

ENV ACESTEP_ROOT=/opt/ace-step \
    ACESTEP_COMMIT=$ACESTEP_COMMIT \
    IMAGE_TAG=$IMAGE_TAG \
    MODEL_DIR=/opt/ml/model \
    ACESTEP_CHECKPOINTS_DIR=/opt/ml/model \
    HF_HUB_OFFLINE=1 \
    TOKENIZERS_PARALLELISM=false \
    MPLBACKEND=Agg \
    PYTHONUNBUFFERED=1

EXPOSE 8080

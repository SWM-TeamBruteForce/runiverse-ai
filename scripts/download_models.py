"""ACE-Step 1.5 가중치(HF) → model.tar.gz → S3.

사용: python scripts/download_models.py --env dev [--revision <HF 커밋 sha>]
- 디스크: 모델 + 압축본 → 모델 크기의 약 2배 여유 필요
- 출력 S3 URI → deploy/envs/<env>.yaml 의 model_data_url 에 입력
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tarfile
import urllib.request
from pathlib import Path
from typing import Callable

from deploy_config import ROOT, load_deploy_config

REPO_ID = "ACE-Step/Ace-Step1.5"
SKIP_NAMES = {".cache"}  # huggingface_hub local_dir 메타데이터
# ACE-Step은 시작 시 이 파일들을 자기 소스(acestep/models/turbo)로 덮어쓴다.
# SageMaker의 /opt/ml/model은 읽기 전용일 수 있어 패키징 때 미리 맞춘다.
MODEL_CODE_DIR = "acestep-v15-turbo"
MODEL_CODE_FILES = ("configuration_acestep_v15.py", "modeling_acestep_v15_turbo.py")
RAW_URL = "https://raw.githubusercontent.com/ace-step/ACE-Step-1.5/{commit}/acestep/models/turbo/{name}"


def model_key(revision: str) -> str:
    return f"music/models/ace-step-1.5/{revision}/model.tar.gz"


def acestep_commit() -> str:
    # 컨테이너와 같은 커밋을 써야 해시가 일치하므로 Dockerfile을 단일 출처로 삼는다
    text = (ROOT / "container" / "Dockerfile").read_text(encoding="utf-8")
    return re.search(r"^ARG ACESTEP_COMMIT=(\w+)", text, re.M).group(1)


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read()


def sync_model_code(checkpoints: Path, commit: str, fetch: Callable[[str], bytes] = _fetch) -> list[str]:
    target = checkpoints / MODEL_CODE_DIR
    for name in MODEL_CODE_FILES:
        (target / name).write_bytes(fetch(RAW_URL.format(commit=commit, name=name)))
    return list(MODEL_CODE_FILES)


def write_revisions(checkpoints: Path, revisions: dict[str, str]) -> None:
    (checkpoints / "model_revisions.json").write_text(json.dumps(revisions, indent=2), encoding="utf-8")


def make_tarball(checkpoints: Path, tar_path: Path) -> None:
    # SageMaker가 /opt/ml/model에 풀므로 압축 루트 = checkpoints 내용
    # safetensors는 거의 압축되지 않아 속도 우선(compresslevel=1)
    with tarfile.open(tar_path, "w:gz", compresslevel=1) as tar:
        for item in sorted(checkpoints.iterdir()):
            if item.name not in SKIP_NAMES:
                tar.add(item, arcname=item.name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ACE-Step 가중치를 S3 model.tar.gz로 올린다.")
    parser.add_argument("--env", default="dev")
    parser.add_argument("--revision", help="HF 커밋 sha (생략 시 현재 main)")
    parser.add_argument("--workdir", type=Path, default=ROOT / ".model_build")
    args = parser.parse_args(argv)

    cfg = load_deploy_config(args.env)
    if not cfg.bucket:
        print(f"deploy/envs/{args.env}.yaml의 bucket을 먼저 채우세요.", file=sys.stderr)
        return 2

    import boto3
    from huggingface_hub import HfApi, snapshot_download

    revision = args.revision or HfApi().model_info(REPO_ID).sha
    build_dir = args.workdir / revision
    checkpoints = build_dir / "checkpoints"
    print(f"다운로드: {REPO_ID}@{revision}", flush=True)
    snapshot_download(repo_id=REPO_ID, revision=revision, local_dir=str(checkpoints))
    commit = acestep_commit()
    print(f"모델 코드 동기화: ACE-Step@{commit[:7]}", flush=True)
    sync_model_code(checkpoints, commit)
    write_revisions(checkpoints, {REPO_ID: revision, "acestep_model_code": commit})

    tar_path = build_dir / "model.tar.gz"
    print("압축 중 (수 분 소요)...", flush=True)
    make_tarball(checkpoints, tar_path)

    key = model_key(revision)
    print(f"업로드: s3://{cfg.bucket}/{key}", flush=True)
    boto3.client("s3", region_name=cfg.region).upload_file(str(tar_path), cfg.bucket, key)
    print(f"완료 → deploy/envs/{args.env}.yaml 의 model_data_url 에 입력:\ns3://{cfg.bucket}/{key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

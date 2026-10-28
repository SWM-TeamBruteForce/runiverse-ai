"""deploy/envs/<env>.yaml 로더. 배포 스크립트 공용.

사용: python scripts/deploy_config.py dev region  → 값 출력
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
LOCKED_ENV = {"GENERATOR": "acestep", "STORAGE": "s3"}
REQUIRED_FOR_DEPLOY = ("bucket", "execution_role_arn", "model_data_url")


class DeployConfigError(ValueError):
    """배포 설정 오류."""


@dataclass(frozen=True)
class Autoscaling:
    min_capacity: int
    max_capacity: int
    target_backlog_per_instance: float
    scale_in_cooldown: int
    scale_out_cooldown: int


@dataclass(frozen=True)
class DeployConfig:
    env_name: str
    region: str
    endpoint_name: str
    ecr_repository: str
    instance_type: str
    initial_instance_count: int
    max_concurrent_invocations_per_instance: int
    model_data_download_timeout: int
    container_startup_health_check_timeout: int
    autoscaling: Autoscaling
    bucket: str
    execution_role_arn: str
    model_data_url: str
    sns_success_topic_arn: str
    sns_error_topic_arn: str
    inference_ami_version: str
    container_env: dict[str, str]


def _env_value(value) -> str:
    # YAML 불리언(true)이 "True"로 바뀌면 컨테이너 설정 검증에서 실패하므로 소문자로
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def load_deploy_config(env_name: str, root: Path = ROOT) -> DeployConfig:
    path = Path(root) / "deploy" / "envs" / f"{env_name}.yaml"
    if not path.is_file():
        raise DeployConfigError(f"설정 파일이 없습니다: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    try:
        autoscaling = Autoscaling(**raw.pop("autoscaling"))
        container_env = {str(k): _env_value(v) for k, v in (raw.pop("container_env") or {}).items()}
        fields = {k: ("" if v is None else v) for k, v in raw.items()}
        cfg = DeployConfig(env_name=env_name, autoscaling=autoscaling, container_env=container_env, **fields)
    except (KeyError, TypeError) as exc:
        raise DeployConfigError(f"{path} 형식 오류: {exc}") from exc
    for key, allowed in LOCKED_ENV.items():
        if cfg.container_env.get(key, allowed) != allowed:
            raise DeployConfigError(f"{key}는 배포 환경에서 {allowed}만 허용합니다.")
    return cfg


def require_deployable(cfg: DeployConfig) -> None:
    missing = [key for key in REQUIRED_FOR_DEPLOY if not getattr(cfg, key)]
    if missing:
        raise DeployConfigError(f"deploy/envs/{cfg.env_name}.yaml에 값을 채우세요: {', '.join(missing)}")


def main(argv: list[str]) -> int:
    env_name, field = argv
    print(getattr(load_deploy_config(env_name), field))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

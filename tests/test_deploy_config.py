from pathlib import Path

import pytest
import yaml

from deploy_config import DeployConfigError, load_deploy_config, require_deployable

REPO_ROOT = Path(__file__).resolve().parents[1]


def env_with(tmp_path, container_env=None, **changes):
    raw = yaml.safe_load((REPO_ROOT / "deploy/envs/dev.yaml").read_text(encoding="utf-8"))
    raw.update(changes)
    if container_env:
        raw["container_env"].update(container_env)
    target = tmp_path / "deploy" / "envs"
    target.mkdir(parents=True)
    (target / "dev.yaml").write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return tmp_path


def test_repo_dev_yaml_loads():
    cfg = load_deploy_config("dev", REPO_ROOT)
    assert cfg.endpoint_name == "runiverse-music-dev"
    assert cfg.region == "ap-northeast-2"
    assert cfg.instance_type == "ml.g4dn.xlarge"
    assert (cfg.autoscaling.min_capacity, cfg.autoscaling.max_capacity) == (0, 1)
    assert cfg.container_env["MAX_DURATION_SECONDS"] == "120"
    assert cfg.bucket == ""


def test_missing_env_file(tmp_path):
    with pytest.raises(DeployConfigError, match="설정 파일"):
        load_deploy_config("prod", tmp_path)


@pytest.mark.parametrize("env", [{"GENERATOR": "fake"}, {"STORAGE": "local"}])
def test_rejects_test_only_modes(tmp_path, env):
    with pytest.raises(DeployConfigError, match="만 허용"):
        load_deploy_config("dev", env_with(tmp_path, container_env=env))


def test_unquoted_bool_becomes_lowercase(tmp_path):
    cfg = load_deploy_config("dev", env_with(tmp_path, container_env={"OFFLOAD_TO_CPU": True}))
    assert cfg.container_env["OFFLOAD_TO_CPU"] == "true"


def test_unknown_key_rejected(tmp_path):
    with pytest.raises(DeployConfigError, match="형식"):
        load_deploy_config("dev", env_with(tmp_path, instanse_type="typo"))


def test_require_deployable_lists_missing():
    cfg = load_deploy_config("dev", REPO_ROOT)
    with pytest.raises(DeployConfigError) as info:
        require_deployable(cfg)
    for key in ("bucket", "execution_role_arn", "model_data_url"):
        assert key in str(info.value)


def test_require_deployable_ok(tmp_path):
    root = env_with(
        tmp_path, bucket="bkt", execution_role_arn="arn:aws:iam::1:role/r",
        model_data_url="s3://bkt/m/model.tar.gz",
    )
    require_deployable(load_deploy_config("dev", root))

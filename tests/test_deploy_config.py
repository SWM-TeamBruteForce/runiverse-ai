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


def write_local(root, env="dev", **values):
    (Path(root) / "deploy" / "envs" / f"{env}.local.yaml").write_text(yaml.safe_dump(values), encoding="utf-8")


def test_repo_env_files_have_no_account_values():
    # 공개 저장소: 계정 ID·역할 ARN·버킷 이름은 <env>.local.yaml(git 제외)에만 둔다
    for env in ("dev", "prod"):
        raw = yaml.safe_load((REPO_ROOT / f"deploy/envs/{env}.yaml").read_text(encoding="utf-8"))
        assert (raw["bucket"], raw["execution_role_arn"], raw["model_data_url"]) == ("", "", ""), env
        assert raw["container_env"]["PHOTO_BUCKET"] == "", env


def test_repo_dev_yaml_loads(tmp_path):
    cfg = load_deploy_config("dev", env_with(tmp_path))  # 저장소 파일만 (로컬 값 제외)
    assert cfg.endpoint_name == "runiverse-music-test-v1"
    assert cfg.region == "ap-northeast-2"
    assert cfg.instance_type == "ml.g4dn.2xlarge"  # xlarge(RAM 16GB)는 시작 중 메모리 부족
    assert (cfg.autoscaling.min_capacity, cfg.autoscaling.max_capacity) == (0, 1)
    assert cfg.s3_env == "test"
    assert cfg.container_env["OUTPUT_PREFIX"] == "audio/test"


def test_repo_prod_yaml_loads(tmp_path):
    target = tmp_path / "deploy" / "envs"
    target.mkdir(parents=True)
    (target / "prod.yaml").write_text((REPO_ROOT / "deploy/envs/prod.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    cfg = load_deploy_config("prod", tmp_path)
    assert cfg.s3_env == "prod"
    assert cfg.container_env["OUTPUT_PREFIX"] == "audio"


def test_local_file_fills_account_values(tmp_path):
    root = env_with(tmp_path)
    write_local(root, bucket="bkt", execution_role_arn="arn:aws:iam::1:role/r",
                model_data_url="s3://bkt/m/model.tar.gz", container_env={"PHOTO_BUCKET": "photos"})

    cfg = load_deploy_config("dev", root)

    assert (cfg.bucket, cfg.execution_role_arn, cfg.model_data_url) ==         ("bkt", "arn:aws:iam::1:role/r", "s3://bkt/m/model.tar.gz")
    assert cfg.container_env["PHOTO_BUCKET"] == "photos"
    assert cfg.container_env["OUTPUT_PREFIX"] == "audio/test"  # 공용 값은 저장소 파일 것 유지
    require_deployable(cfg)


def test_local_file_unknown_key_rejected(tmp_path):
    root = env_with(tmp_path)
    write_local(root, buket="typo")

    with pytest.raises(DeployConfigError, match="형식"):
        load_deploy_config("dev", root)


def test_local_file_cannot_unlock_test_modes(tmp_path):
    root = env_with(tmp_path)
    write_local(root, container_env={"ANALYZER": "fake"})

    with pytest.raises(DeployConfigError, match="만 허용"):
        load_deploy_config("dev", root)


def test_require_deployable_points_to_local_file(tmp_path):
    with pytest.raises(DeployConfigError, match=r"dev\.local\.yaml"):
        require_deployable(load_deploy_config("dev", env_with(tmp_path)))


def test_missing_env_file(tmp_path):
    with pytest.raises(DeployConfigError, match="설정 파일"):
        load_deploy_config("prod", tmp_path)


@pytest.mark.parametrize("env", [{"ANALYZER": "fake"}, {"GENERATOR": "fake"}, {"STORAGE": "local"}])
def test_rejects_test_only_modes(tmp_path, env):
    with pytest.raises(DeployConfigError, match="만 허용"):
        load_deploy_config("dev", env_with(tmp_path, container_env=env))


def test_unquoted_bool_becomes_lowercase(tmp_path):
    cfg = load_deploy_config("dev", env_with(tmp_path, container_env={"OFFLOAD_TO_CPU": True}))
    assert cfg.container_env["OFFLOAD_TO_CPU"] == "true"


def test_unknown_key_rejected(tmp_path):
    with pytest.raises(DeployConfigError, match="형식"):
        load_deploy_config("dev", env_with(tmp_path, instanse_type="typo"))


def test_require_deployable_lists_missing(tmp_path):
    root = env_with(tmp_path, container_env={"PHOTO_BUCKET": ""}, bucket="", s3_env="",
                    execution_role_arn="", model_data_url="")
    with pytest.raises(DeployConfigError) as info:
        require_deployable(load_deploy_config("dev", root))
    for key in ("bucket", "s3_env", "execution_role_arn", "model_data_url", "container_env.PHOTO_BUCKET"):
        assert key in str(info.value)


def test_require_deployable_ok(tmp_path):
    root = env_with(
        tmp_path, container_env={"PHOTO_BUCKET": "photos"}, bucket="bkt",
        execution_role_arn="arn:aws:iam::1:role/r", model_data_url="s3://bkt/m/model.tar.gz",
    )
    require_deployable(load_deploy_config("dev", root))

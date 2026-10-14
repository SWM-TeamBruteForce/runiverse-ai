import json
import tarfile

import download_models as dm


def test_model_key():
    assert dm.model_key("abc") == "music/models/ace-step-1.5/abc/model.tar.gz"


def test_tarball_root_layout(tmp_path):
    ck = tmp_path / "checkpoints"
    (ck / "acestep-v15-turbo").mkdir(parents=True)
    (ck / "acestep-v15-turbo" / "model.safetensors").write_bytes(b"w")
    (ck / ".cache" / "huggingface").mkdir(parents=True)
    (ck / ".cache" / "huggingface" / "meta").write_text("m")
    dm.write_revisions(ck, {dm.REPO_ID: "abc"})

    tar_path = tmp_path / "model.tar.gz"
    dm.make_tarball(ck, tar_path)

    with tarfile.open(tar_path) as tar:
        assert sorted(tar.getnames()) == [
            "acestep-v15-turbo",
            "acestep-v15-turbo/model.safetensors",
            "model_revisions.json",
        ]
        assert json.load(tar.extractfile("model_revisions.json")) == {dm.REPO_ID: "abc"}


def test_acestep_commit_matches_dockerfile():
    assert dm.acestep_commit() == "ca1e85fe9430179831e6bc6be790c332190a3866"


def test_sync_model_code_overwrites_with_pinned_source(tmp_path):
    # /opt/ml/model이 읽기 전용이라 ACE-Step의 시작 시 자동 동기화(덮어쓰기)를 미리 해 둔다
    ck = tmp_path / "checkpoints"
    (ck / "acestep-v15-turbo").mkdir(parents=True)
    (ck / "acestep-v15-turbo" / "configuration_acestep_v15.py").write_text("hf")
    requested = []

    def fetch(url: str) -> bytes:
        requested.append(url)
        return b"pinned:" + url.rsplit("/", 1)[1].encode()

    synced = dm.sync_model_code(ck, "abc", fetch=fetch)

    assert synced == list(dm.MODEL_CODE_FILES)
    assert requested == [
        f"https://raw.githubusercontent.com/ace-step/ACE-Step-1.5/abc/acestep/models/turbo/{name}"
        for name in dm.MODEL_CODE_FILES
    ]
    for name in dm.MODEL_CODE_FILES:
        assert (ck / "acestep-v15-turbo" / name).read_bytes() == f"pinned:{name}".encode()

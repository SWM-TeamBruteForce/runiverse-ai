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

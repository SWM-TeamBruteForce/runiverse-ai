import pytest
from botocore.exceptions import ClientError

from runiverse_music.config import load_settings
from runiverse_music.storage import LocalStorage, S3Storage, StorageError, build_key, make_storage


class FakeS3:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def upload_file(self, filename, bucket, key, ExtraArgs=None):
        self.calls.append((filename, bucket, key, ExtraArgs))
        if self.error:
            raise self.error


def test_build_key():
    assert build_key("music/outputs", "req-1", ".wav") == "music/outputs/req-1/req-1.wav"
    assert build_key("/music/outputs/", "r", ".wav") == "music/outputs/r/r.wav"


def test_local_storage_copies(tmp_path):
    src = tmp_path / "a.wav"
    src.write_bytes(b"RIFF")
    LocalStorage(tmp_path / "out").upload(src, "music/outputs/r/r.wav")
    assert (tmp_path / "out/music/outputs/r/r.wav").read_bytes() == b"RIFF"


def test_local_storage_overwrites_same_key(tmp_path):
    storage = LocalStorage(tmp_path / "out")
    src = tmp_path / "a.wav"
    src.write_bytes(b"first")
    storage.upload(src, "k/r.wav")
    src.write_bytes(b"second")
    storage.upload(src, "k/r.wav")
    assert (tmp_path / "out/k/r.wav").read_bytes() == b"second"


def test_s3_storage_uploads(tmp_path):
    src = tmp_path / "a.wav"
    src.write_bytes(b"RIFF")
    client = FakeS3()
    S3Storage("bkt", client=client).upload(src, "k.wav")
    assert client.calls == [(str(src), "bkt", "k.wav", {"ContentType": "audio/wav"})]


def test_s3_storage_wraps_errors(tmp_path):
    src = tmp_path / "a.wav"
    src.write_bytes(b"RIFF")
    error = ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "PutObject")
    with pytest.raises(StorageError, match="s3://bkt/k.wav"):
        S3Storage("bkt", client=FakeS3(error)).upload(src, "k.wav")


def test_make_storage_local(tmp_path):
    settings = load_settings({"STORAGE": "local", "LOCAL_OUTPUT_DIR": str(tmp_path)})
    assert isinstance(make_storage(settings), LocalStorage)

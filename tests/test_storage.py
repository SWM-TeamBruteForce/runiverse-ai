import io

import pytest
from botocore.exceptions import ClientError

from runiverse_music.config import load_settings
from runiverse_music.storage import LocalStorage, S3Storage, StorageError, make_storage, music_key


class FakeS3:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def upload_file(self, filename, bucket, key, ExtraArgs=None):
        self.calls.append((filename, bucket, key, ExtraArgs))
        if self.error:
            raise self.error

    def get_object(self, Bucket, Key):
        self.calls.append((Bucket, Key))
        if self.error:
            raise self.error
        return {"Body": io.BytesIO(b"jpg")}


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
    S3Storage("bkt", "photos", client=client).upload(src, "k.wav")
    assert client.calls == [(str(src), "bkt", "k.wav", {"ContentType": "audio/wav"})]


def test_s3_storage_wraps_errors(tmp_path):
    src = tmp_path / "a.wav"
    src.write_bytes(b"RIFF")
    error = ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "PutObject")
    with pytest.raises(StorageError, match="s3://bkt/k.wav"):
        S3Storage("bkt", "photos", client=FakeS3(error)).upload(src, "k.wav")


def test_make_storage_local(tmp_path):
    settings = load_settings({"STORAGE": "local", "LOCAL_OUTPUT_DIR": str(tmp_path)})
    assert isinstance(make_storage(settings), LocalStorage)


def test_music_key_follows_s3_contract():
    # {OUTPUT_PREFIX}/{user_id}/{activity_type}/{record_card_id}/base.wav | {record_card_image_id}.wav
    assert music_key("audio/test", 123, "TRAVEL", 101, None) == "audio/test/123/TRAVEL/101/base.wav"
    assert music_key("audio", 123, "TRAVEL", 101, 201) == "audio/123/TRAVEL/101/201.wav"  # prod
    assert music_key("/audio/test/", 1, "RACE", 2, 3) == "audio/test/1/RACE/2/3.wav"


def test_s3_storage_reads_photo_from_photo_bucket():
    client = FakeS3()
    assert S3Storage("bkt", "photos", client=client).read_photo("cards/101/photos/201.jpg") == b"jpg"
    assert client.calls == [("photos", "cards/101/photos/201.jpg")]


def test_s3_read_photo_wraps_errors():
    error = ClientError({"Error": {"Code": "NoSuchKey", "Message": "none"}}, "GetObject")
    with pytest.raises(StorageError, match="s3://photos/k.jpg"):
        S3Storage("bkt", "photos", client=FakeS3(error)).read_photo("k.jpg")


def test_local_storage_reads_photo_under_root(tmp_path):
    (tmp_path / "cards").mkdir()
    (tmp_path / "cards/1.jpg").write_bytes(b"jpg")
    assert LocalStorage(tmp_path).read_photo("cards/1.jpg") == b"jpg"

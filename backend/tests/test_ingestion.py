import pytest
from app.services.ingestion import safe_filename, upload_file


def test_filename_is_sanitized():
    assert safe_filename("../../hello world.csv") == "hello world.csv"


def test_unsupported_extension_rejected(tmp_path, monkeypatch):
    from app.services import ingestion
    monkeypatch.setattr(ingestion, "UPLOAD_DIR", tmp_path)
    with pytest.raises(ValueError, match="Unsupported file type"):
        upload_file("malware.exe", b"not an exe")


def test_empty_upload_rejected(tmp_path, monkeypatch):
    from app.services import ingestion
    monkeypatch.setattr(ingestion, "UPLOAD_DIR", tmp_path)
    with pytest.raises(ValueError, match="empty"):
        upload_file("empty.csv", b"")

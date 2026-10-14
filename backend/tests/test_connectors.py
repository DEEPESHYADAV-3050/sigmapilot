import io
import json
import pytest
from app.services import connectors


def test_connector_requires_https():
    with pytest.raises(ValueError, match="HTTPS"):
        connectors._validate_public_https_url("http://example.com/data")
    with pytest.raises(ValueError, match="blocked"):
        connectors._validate_public_https_url("https://127.0.0.1/private")


def test_connector_import_parses_records_without_persisting_token(monkeypatch):
    class FakeResponse:
        headers = {"Content-Type": "application/json"}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, n): return json.dumps({"data": [{"x": 1}, {"x": 2}]}).encode()
    class FakeOpener:
        def open(self, request, timeout):
            assert request.get_header("Authorization") == "Bearer secret-token"
            assert timeout == 15
            return FakeResponse()
    monkeypatch.setattr(connectors, "_validate_public_https_url", lambda url: "example.com")
    monkeypatch.setattr(connectors.urllib.request, "build_opener", lambda handler: FakeOpener())
    monkeypatch.setattr(connectors, "upload_file", lambda filename, content: {"kind": "dataset", "id": "test", "filename": filename, "row_count": 2, "columns": ["x"]})
    result = connectors.import_http_json("https://example.com/data", "secret-token", "remote.csv")
    assert result["row_count"] == 2
    assert result["connector"]["credential_persisted"] is False
    assert "secret-token" not in json.dumps(result)

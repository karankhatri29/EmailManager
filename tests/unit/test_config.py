import json

import pytest

from app.core.config import Settings


def _settings(**kwargs):
    # Ignore any .env / environment values for the fields under test.
    base = {"google_client_id": "", "google_client_secret": ""}
    return Settings(_env_file=None, **{**base, **kwargs})


def test_google_client_from_settings():
    assert _settings(google_client_id="id", google_client_secret="sec").google_client() == ("id", "sec")


@pytest.mark.parametrize("kind", ["installed", "web"])
def test_google_client_from_credentials_file(tmp_path, kind):
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps({kind: {"client_id": "file-id", "client_secret": "file-secret"}}))
    assert _settings(google_credentials_path=path).google_client() == ("file-id", "file-secret")


def test_settings_take_priority_over_file(tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps({"web": {"client_id": "file-id", "client_secret": "file-secret"}}))
    s = _settings(google_client_id="id", google_client_secret="sec", google_credentials_path=path)
    assert s.google_client() == ("id", "sec")


def test_google_client_missing_raises(tmp_path):
    with pytest.raises(RuntimeError, match="not configured"):
        _settings(google_credentials_path=tmp_path / "missing.json").google_client()

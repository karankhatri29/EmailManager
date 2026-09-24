def test_app_shell_is_served_without_login(client):
    r = client.get("/")
    assert r.status_code == 200 and "Email Prioritizer" in r.text
    assert "/static/js/main.js" in r.text


def test_static_assets_are_served(client):
    assert client.get("/static/js/main.js").status_code == 200


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_api_docs_are_available(client):
    assert client.get("/openapi.json").status_code == 200

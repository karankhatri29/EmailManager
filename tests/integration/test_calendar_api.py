from icalendar import Calendar


def _create(client, **fields):
    return client.post("/api/activities", json={"title": "Task", **fields}).json()


def _feed_path(client):
    url = client.get("/api/calendar/feed").json()["url"]
    assert url.startswith("http://testserver/calendar/") and url.endswith(".ics")
    return url.removeprefix("http://testserver")


def _summaries(ics: bytes):
    return sorted(str(e["summary"]) for e in Calendar.from_ical(ics).walk() if e.name == "VEVENT")


def test_feed_url_is_stable_and_private(auth_client, bob_client):
    assert _feed_path(auth_client) == _feed_path(auth_client)
    assert _feed_path(auth_client) != _feed_path(bob_client)


def test_feed_is_served_without_login_as_calendar_data(auth_client, make_client):
    _create(auth_client, title="Report due", start_at="2026-10-15T00:00:00Z", all_day=True)
    path = _feed_path(auth_client)

    r = make_client().get(path)  # a calendar app: no cookies
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    assert r.headers["cache-control"] == "no-store"
    assert _summaries(r.content) == ["Report due"]


def test_feed_contains_only_open_scheduled_activities_of_that_user(auth_client, bob_client):
    _create(auth_client, title="open", start_at="2026-10-15T00:00:00Z", all_day=True)
    done = _create(auth_client, title="done", start_at="2026-10-16T00:00:00Z", all_day=True)
    auth_client.patch(f"/api/activities/{done['id']}", json={"status": "done"})
    _create(auth_client, title="backlog")
    _create(bob_client, title="bobs", start_at="2026-10-15T00:00:00Z", all_day=True)

    assert _summaries(auth_client.get(_feed_path(auth_client)).content) == ["open"]
    assert _summaries(bob_client.get(_feed_path(bob_client)).content) == ["bobs"]


def test_feed_reflects_edits(auth_client):
    a = _create(auth_client, title="Before", start_at="2026-10-15T00:00:00Z", all_day=True)
    path = _feed_path(auth_client)
    auth_client.patch(f"/api/activities/{a['id']}", json={"title": "After"})
    assert _summaries(auth_client.get(path).content) == ["After"]


def test_empty_feed_is_valid(auth_client):
    assert _summaries(auth_client.get(_feed_path(auth_client)).content) == []


def test_unknown_token_is_404(client):
    assert client.get("/calendar/not-a-real-token.ics").status_code == 404


def test_rotating_invalidates_the_old_link(auth_client):
    _create(auth_client, title="x", start_at="2026-10-15T00:00:00Z", all_day=True)
    old = _feed_path(auth_client)
    new = auth_client.post("/api/calendar/feed/rotate").json()["url"].removeprefix("http://testserver")

    assert new != old
    assert auth_client.get(old).status_code == 404
    assert auth_client.get(new).status_code == 200
    assert _feed_path(auth_client) == new

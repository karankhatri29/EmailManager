from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.api import mail as mail_api
from app.core.security import LoginRateLimiter
from app.repositories import emails as repo
from tests.conftest import stored_email

PLAN = "app.services.search_service.ai_summarizer.generate_json"
QUERY_VECTOR = "app.services.search_service.embeddings.embed_query"
THREAD_AI = "app.services.threads.ai_summarizer.summarize_thread"


def _now(**delta):
    return datetime.now(timezone.utc) + timedelta(**delta)


def _plan(**fields):
    return {"keywords": [], "date_from": None, "date_to": None, "category": None, "sender": None, **fields}


def _seed(db, account, *emails):
    repo.upsert_many(db, [stored_email(account, mid, **extra) for mid, extra in emails])


# --- smart search --------------------------------------------------------------------------------


def test_search_returns_the_plan_and_ranked_results(auth_client, db, account):
    _seed(db, account, ("invoice", {"subject": "Blue couch invoice"}), ("lunch", {"subject": "Lunch"}))
    with patch(PLAN, return_value=_plan(keywords=["couch"], date_from="2026-06-01", date_to="2026-08-31")), patch(QUERY_VECTOR, return_value=None):
        r = auth_client.get("/api/search", params={"q": "invoice for the blue couch last summer"})

    body = r.json()
    assert r.status_code == 200 and body["semantic"] is False
    assert body["plan"]["keywords"] == ["couch"] and body["plan"]["used_ai"] is True
    assert body["plan"]["date_from"] == "2026-06-01" and "couch" in body["plan"]["explanation"]
    assert body["items"] == []  # mail from this week is outside the plan's date range

    with patch(PLAN, return_value=_plan(keywords=["couch"])), patch(QUERY_VECTOR, return_value=None):
        items = auth_client.get("/api/search", params={"q": "blue couch"}).json()["items"]
    assert [i["id"].split(":")[1] for i in items] == ["invoice"]
    assert items[0]["match"] > 0 and items[0]["subject"] == "Blue couch invoice" and "snippet" in items[0]


def test_search_works_without_the_ai(auth_client, db, account):
    _seed(db, account, ("a", {"subject": "Blue couch invoice"}))
    with patch(PLAN, side_effect=RuntimeError("no key")), patch(QUERY_VECTOR, return_value=None):
        body = auth_client.get("/api/search", params={"q": "find the blue couch"}).json()
    assert body["plan"]["used_ai"] is False and [i["id"].split(":")[1] for i in body["items"]] == ["a"]


def test_search_validation_and_mailbox_scope(auth_client, bob_client, bob_account):
    assert auth_client.get("/api/search").status_code == 422
    assert auth_client.get("/api/search", params={"q": "x"}).status_code == 422  # too short
    assert auth_client.get("/api/search", params={"q": "couch" * 100}).status_code == 422  # too long
    assert auth_client.get("/api/search", params={"q": "couch", "limit": 500}).status_code == 422
    assert auth_client.get("/api/search", params={"q": "couch", "account_id": bob_account.id}).status_code == 404


def test_search_never_returns_other_users_mail(auth_client, bob_client, db, account, bob_account):
    _seed(db, account, ("mine", {"subject": "Couch invoice"}))
    repo.upsert_many(db, [stored_email(bob_account, "bobs", subject="Couch invoice")])
    with patch(PLAN, return_value=_plan(keywords=["couch"])), patch(QUERY_VECTOR, return_value=None):
        ids = lambda c: [i["id"].split(":")[1] for i in c.get("/api/search", params={"q": "couch"}).json()["items"]]  # noqa: E731
        assert ids(auth_client) == ["mine"] and ids(bob_client) == ["bobs"]


def test_search_is_rate_limited_per_user(auth_client, bob_client, db, account):
    limiter = LoginRateLimiter(max_failures=2, window_seconds=60)
    with patch.object(mail_api, "ai_limiter", limiter), patch(PLAN, return_value=_plan(keywords=["x"])), patch(QUERY_VECTOR, return_value=None):
        assert auth_client.get("/api/search", params={"q": "couch"}).status_code == 200
        assert auth_client.get("/api/search", params={"q": "couch"}).status_code == 200
        assert auth_client.get("/api/search", params={"q": "couch"}).status_code == 429
        assert bob_client.get("/api/search", params={"q": "couch"}).status_code == 200  # another user's budget


# --- conversations -------------------------------------------------------------------------------


def _thread(db, account, thread_id="t1", n=3):
    _seed(
        db,
        account,
        *[(f"{thread_id}-{i}", {"thread_id": thread_id, "date": _now(hours=-(n - i)), "subject": "Project", "body": f"message {i}"}) for i in range(n)],
    )
    return f"{account.id}:{thread_id}-0"


def test_thread_lists_the_conversation_oldest_first(auth_client, db, account):
    first = _thread(db, account)
    _seed(db, account, ("other", {"thread_id": "t2"}))
    data = auth_client.get(f"/api/emails/{first}/thread").json()
    assert data["message_count"] == 3 and data["summary"] is None and data["summary_current"] is False
    assert [m["snippet"] for m in data["messages"]] == ["message 0", "message 1", "message 2"]
    assert data["messages"][0]["date"].endswith("Z")


def test_an_email_without_a_thread_is_a_conversation_of_one(auth_client, db, account):
    _seed(db, account, ("solo", {}))
    assert auth_client.get(f"/api/emails/{account.id}:solo/thread").json()["message_count"] == 1


def test_summarising_a_thread_is_cached_until_it_grows(auth_client, db, account):
    first = _thread(db, account, n=3)
    with patch(THREAD_AI, return_value="<p>SUMMARY</p>") as ai:
        r = auth_client.post(f"/api/emails/{first}/thread/summary")
        assert r.status_code == 200 and r.json()["summary"] == "<p>SUMMARY</p>" and r.json()["summary_current"] is True
        auth_client.post(f"/api/emails/{first}/thread/summary")
        assert ai.call_count == 1  # the second request reused the stored summary

        transcript = ai.call_args.args[0]
        assert transcript.index("message 0") < transcript.index("message 2") and "[1] From" in transcript

        _seed(db, account, ("t1-new", {"thread_id": "t1", "date": _now(), "body": "a new reply"}))
        stale = auth_client.get(f"/api/emails/{first}/thread").json()
        assert stale["summary"] == "<p>SUMMARY</p>" and stale["summary_current"] is False

    with patch(THREAD_AI, return_value="<p>UPDATED</p>") as ai:
        fresh = auth_client.post(f"/api/emails/{first}/thread/summary").json()
    assert ai.call_count == 1 and fresh["summary"] == "<p>UPDATED</p>" and fresh["message_count"] == 4


def test_a_single_message_has_nothing_to_summarise(auth_client, db, account):
    _seed(db, account, ("solo", {"thread_id": "only"}))
    with patch(THREAD_AI) as ai:
        assert auth_client.post(f"/api/emails/{account.id}:solo/thread/summary").status_code == 400
    ai.assert_not_called()


def test_ai_failure_is_reported_not_crashed(auth_client, db, account):
    first = _thread(db, account)
    with patch(THREAD_AI, side_effect=RuntimeError("quota")):
        r = auth_client.post(f"/api/emails/{first}/thread/summary")
    assert r.status_code == 502
    assert auth_client.get(f"/api/emails/{first}/thread").json()["summary"] is None


def test_threads_are_private(auth_client, bob_client, db, account):
    first = _thread(db, account)
    assert bob_client.get(f"/api/emails/{first}/thread").status_code == 404
    assert bob_client.post(f"/api/emails/{first}/thread/summary").status_code == 404


def test_the_same_thread_id_in_two_mailboxes_is_two_conversations(auth_client, db, user, account):
    from tests.conftest import make_account

    other = make_account(db, user, "second@gmail.com")
    a = _thread(db, account, "shared", n=2)
    repo.upsert_many(db, [stored_email(other, "x", thread_id="shared", body="elsewhere")])
    assert auth_client.get(f"/api/emails/{a}/thread").json()["message_count"] == 2
    assert auth_client.get(f"/api/emails/{other.id}:x/thread").json()["message_count"] == 1

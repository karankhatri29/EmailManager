import pytest

from app.db.models import Activity
from app.repositories import emails as repo
from tests.conftest import stored_email

URGENT = "Urgent / Action Required"


def _seed(db, account, *emails):
    repo.upsert_many(db, [stored_email(account, mid, **extra) for mid, extra in emails])


def _get(client, account, mid):
    return client.get(f"/api/emails/{account.id}:{mid}").json()


def test_no_rules_at_first(auth_client):
    assert auth_client.get("/api/rules").json() == []


def test_sender_rule_reclassifies_existing_mail_and_explains_itself(auth_client, db, account):
    boss = "Boss <boss@work.com>"
    _seed(db, account, ("a", {"sender": boss}), ("b", {"sender": boss}), ("c", {"sender": "Bob <bob@x.com>"}))

    r = auth_client.post("/api/rules", json={"kind": "sender", "pattern": "Boss@Work.com", "category": URGENT})
    body = r.json()
    assert r.status_code == 201 and body["pattern"] == "boss@work.com" and body["affected"] == 2

    a = _get(auth_client, account, "a")
    assert a["category"] == URGENT and a["category_source"] == "rule" and a["reason"] == (
        "Your rule: mail from boss@work.com is always Urgent / Action Required."
    )
    assert a["task"] is not None
    assert _get(auth_client, account, "c")["category"] == "General"
    assert db.query(Activity).count() == 2  # rule-promoted mail shows up in the calendar too


def test_domain_and_keyword_rules(auth_client, db, account):
    _seed(
        db,
        account,
        ("d", {"sender": "News <news@mail.shop.com>"}),
        ("k", {"subject": "Your invoice is ready", "sender": "X <x@y.com>"}),
        ("n", {"subject": "Hello", "sender": "X <x@y.com>"}),
    )
    assert auth_client.post("/api/rules", json={"kind": "domain", "pattern": "shop.com", "category": "Promotional"}).json()["affected"] == 1
    assert auth_client.post("/api/rules", json={"kind": "keyword", "pattern": "invoice", "category": "Important"}).json()["affected"] == 1
    assert _get(auth_client, account, "d")["category"] == "Promotional"
    assert _get(auth_client, account, "k")["category"] == "Important"
    assert _get(auth_client, account, "n")["category"] == "General"


def test_rules_never_override_your_own_corrections(auth_client, db, account):
    _seed(db, account, ("mine", {"sender": "Shop <s@shop.com>", "category": "Important", "category_source": "user"}))
    r = auth_client.post("/api/rules", json={"kind": "sender", "pattern": "s@shop.com", "category": "Promotional"})
    assert r.json()["affected"] == 0
    assert _get(auth_client, account, "mine")["category"] == "Important"


def test_muting_a_sender_removes_its_open_calendar_items(auth_client, db, account):
    _seed(db, account, ("a", {"sender": "Shop <s@shop.com>", "category": URGENT, "subject": "Task", "body": "pay now"}))
    auth_client.patch(f"/api/emails/{account.id}:a", json={"is_done": False})
    from app.services.activities_service import create_activities_for_emails
    from tests.conftest import stored_email as se

    create_activities_for_emails(db, [se(account, "a", category=URGENT, subject="Task", body="pay now", sender="Shop <s@shop.com>")])
    assert db.query(Activity).count() == 1

    auth_client.post("/api/rules", json={"kind": "sender", "pattern": "s@shop.com", "category": "Promotional"})
    db.expire_all()
    assert db.query(Activity).count() == 0


def test_deleting_a_rule_hands_its_mail_back_to_the_classifier(auth_client, db, account):
    _seed(db, account, ("a", {"sender": "Boss <boss@work.com>", "subject": "Hello", "body": "Lunch was great"}))
    rule = auth_client.post("/api/rules", json={"kind": "sender", "pattern": "boss@work.com", "category": URGENT}).json()
    assert _get(auth_client, account, "a")["category"] == URGENT

    assert auth_client.delete(f"/api/rules/{rule['id']}").status_code == 204
    restored = _get(auth_client, account, "a")
    assert restored["category"] == "General" and restored["category_source"] == "auto"
    assert "No urgent" in restored["reason"]
    assert auth_client.get("/api/rules").json() == []


def test_deleting_one_rule_leaves_other_rules_in_charge(auth_client, db, account):
    _seed(db, account, ("a", {"sender": "Boss <boss@work.com>"}))
    domain = auth_client.post("/api/rules", json={"kind": "domain", "pattern": "work.com", "category": "Important"}).json()
    sender = auth_client.post("/api/rules", json={"kind": "sender", "pattern": "boss@work.com", "category": URGENT}).json()
    assert _get(auth_client, account, "a")["category"] == URGENT  # the sender rule is more specific

    auth_client.delete(f"/api/rules/{sender['id']}")
    assert _get(auth_client, account, "a")["category"] == "Important"  # the domain rule takes over
    auth_client.delete(f"/api/rules/{domain['id']}")
    assert _get(auth_client, account, "a")["category"] == "General"


def test_duplicate_rules_are_rejected(auth_client):
    payload = {"kind": "sender", "pattern": "a@b.com", "category": URGENT}
    assert auth_client.post("/api/rules", json=payload).status_code == 201
    assert auth_client.post("/api/rules", json={**payload, "pattern": "A@B.com"}).status_code == 409
    assert auth_client.post("/api/rules", json={**payload, "category": "Important"}).status_code == 409


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "sender", "pattern": "not-an-address", "category": URGENT},
        {"kind": "domain", "pattern": "localhost", "category": URGENT},
        {"kind": "keyword", "pattern": "ab", "category": URGENT},
        {"kind": "keyword", "pattern": "invoice", "category": "Super"},
        {"kind": "regex", "pattern": ".*", "category": URGENT},
        {"kind": "keyword", "pattern": "", "category": URGENT},
        {"kind": "keyword", "category": URGENT},
    ],
)
def test_invalid_rules_are_rejected(auth_client, payload):
    assert auth_client.post("/api/rules", json=payload).status_code == 422


def test_rule_limit(auth_client, monkeypatch):
    monkeypatch.setattr("app.repositories.rules.MAX_RULES_PER_USER", 2)
    for word in ("alpha", "bravo"):
        assert auth_client.post("/api/rules", json={"kind": "keyword", "pattern": word, "category": URGENT}).status_code == 201
    assert auth_client.post("/api/rules", json={"kind": "keyword", "pattern": "charlie", "category": URGENT}).status_code == 422


def test_rules_are_private(auth_client, bob_client, db, account, bob_account):
    _seed(db, account, ("a", {"sender": "Boss <boss@work.com>"}))
    rule = bob_client.post("/api/rules", json={"kind": "sender", "pattern": "boss@work.com", "category": URGENT}).json()

    assert _get(auth_client, account, "a")["category"] == "General"  # Bob's rule does not touch Alice's mail
    assert auth_client.get("/api/rules").json() == []
    assert auth_client.delete(f"/api/rules/{rule['id']}").status_code == 404
    assert len(bob_client.get("/api/rules").json()) == 1


def test_new_mail_is_classified_with_the_users_rules(auth_client, account, manager):
    from tests.conftest import make_email, patch_provider

    auth_client.post("/api/rules", json={"kind": "sender", "pattern": "boss@work.com", "category": URGENT})
    with patch_provider([make_email("m1", "Hello", "Lunch was great", sender="Boss <boss@work.com>")]), patch(
        "app.services.sync_service.summarize_email", return_value="S"
    ):
        auth_client.post("/api/sync")
        manager.wait(10)
    email = _get(auth_client, account, "m1")
    assert email["category"] == URGENT and email["category_source"] == "rule" and email["summary"] == "S"


from unittest.mock import patch  # noqa: E402

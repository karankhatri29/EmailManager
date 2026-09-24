from unittest.mock import patch

from app.repositories import emails as repo
from tests.conftest import make_email, patch_provider, stored_email
from tests.unit.test_textify import PLACEMENT_EMAIL

SUMM = "app.services.sync_service.summarize_email"


def test_html_mail_is_stored_as_readable_text_when_synced(auth_client, account, manager, db):
    raw = [make_email("m1", "Placement Drive Invitation", PLACEMENT_EMAIL, sender="CDC <cdc@vit.ac.in>")]
    with patch_provider(raw), patch(SUMM, return_value="S"):
        auth_client.post("/api/sync")
        manager.wait(10)

    body = auth_client.get(f"/api/emails/{account.id}:m1").json()["body"]
    assert body.startswith("Placement Drive Invitation") and "Drive Name: LTM" in body
    assert "<div" not in body and "style=" not in body and "&nbsp;" not in body


def test_the_classifier_reads_the_text_not_the_markup(auth_client, account, manager):
    """Words hidden in tag attributes must not influence the category."""
    html = '<div class="sale discount off newsletter"><p>Lunch was great yesterday</p></div>'
    with patch_provider([make_email("m1", "Hello", html)]):
        auth_client.post("/api/sync")
        manager.wait(10)
    assert auth_client.get(f"/api/emails/{account.id}:m1").json()["category"] == "General"


def test_mail_stored_before_the_fix_is_still_shown_as_text(auth_client, db, account):
    repo.upsert_many(db, [stored_email(account, "old", subject="Legacy", body=PLACEMENT_EMAIL)])
    email = auth_client.get(f"/api/emails/{account.id}:old").json()
    assert "Drive Name: LTM" in email["body"] and "<" not in email["body"]

    item = auth_client.get("/api/inbox").json()["items"][0]
    assert item["snippet"].startswith("Placement Drive Invitation Dear Karan") and "<" not in item["snippet"]
    assert len(item["snippet"]) <= 160


def test_dashboard_emails_endpoint_is_readable_too(auth_client, db, account):
    repo.upsert_many(db, [stored_email(account, "old", body=PLACEMENT_EMAIL)])
    (email,) = auth_client.get("/api/emails").json()
    assert "<div" not in email["body"] and "Congratulations!" in email["body"]


def test_thread_snippets_are_readable(auth_client, db, account):
    repo.upsert_many(
        db,
        [
            stored_email(account, "a", thread_id="t", body="<p>First <b>message</b></p>"),
            stored_email(account, "b", thread_id="t", body="<div>Second&nbsp;message</div>"),
        ],
    )
    messages = auth_client.get(f"/api/emails/{account.id}:a/thread").json()["messages"]
    assert sorted(m["snippet"] for m in messages) == ["First message", "Second message"]


def test_ai_summaries_never_show_emoji_even_when_stored_with_them(auth_client, db, account):
    stale = "<div class='x'>\U0001f4dd Core Ask</div><p>Pay the fee ⚡ today</p>"
    repo.upsert_many(db, [stored_email(account, "s", summary=stale)])
    summary = auth_client.get(f"/api/emails/{account.id}:s").json()["summary"]
    assert "Core Ask" in summary and "Pay the fee" in summary
    assert "\U0001f4dd" not in summary and "⚡" not in summary

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.repositories import emails as emails_repo
from tests.conftest import stored_email


def _rows(account):
    return [
        stored_email(
            account,
            f"n{i}",
            sender="News <n@letter.com>",
            category="Promotional",
            is_unread=True,
            unsubscribe_url="https://letter.com/u",
            unsubscribe_one_click=True,
            date=datetime.now(timezone.utc) - timedelta(days=i * 25),
        )
        for i in range(4)
    ]


def test_list_and_clean_up_unopened_newsletters(auth_client, db, account):
    emails_repo.upsert_many(db, _rows(account))
    listed = auth_client.get("/api/newsletters/unopened").json()
    assert [n["sender_address"] for n in listed] == ["n@letter.com"]
    assert listed[0]["messages"] == 4 and listed[0]["one_click"] is True

    with patch("app.services.unsubscribe.one_click_unsubscribe", return_value=True):
        done = auth_client.post(
            "/api/newsletters/unopened/cleanup", json={"sender_addresses": ["N@letter.com"]}
        ).json()
    assert done[0]["unsubscribed"] is True and done[0]["archived"] == 4
    assert auth_client.get("/api/newsletters/unopened").json() == []


def test_cleanup_only_touches_the_chosen_senders(auth_client, db, account):
    emails_repo.upsert_many(db, _rows(account))
    done = auth_client.post(
        "/api/newsletters/unopened/cleanup", json={"sender_addresses": ["other@x.com"]}
    ).json()
    assert done == []


def test_settings_carry_the_new_options(auth_client):
    body = auth_client.put(
        "/api/settings",
        json={"digest_enabled": True, "digest_hour": 20, "auto_cleanup": True, "cleanup_months": 6},
    ).json()
    assert (body["digest_enabled"], body["digest_hour"], body["auto_cleanup"], body["cleanup_months"]) == (
        True,
        20,
        True,
        6,
    )
    assert auth_client.put("/api/settings", json={"cleanup_months": 0}).status_code == 422


def test_the_promotions_digest_endpoint(auth_client):
    body = auth_client.get("/api/digest/promotions").json()
    assert body["count"] == 0 and body["headline"] == "No promotional emails today."

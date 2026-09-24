from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import requests

from app.repositories import emails as repo
from app.services.unsubscribe import UnsafeUrl
from tests.conftest import stored_email

ONE_CLICK = "app.api.mail.one_click_unsubscribe"
SHOP = "Shop <deals@shop.com>"


def _now(**delta):
    return datetime.now(timezone.utc) + timedelta(**delta)


def _seed(db, account, *emails):
    repo.upsert_many(db, [stored_email(account, mid, **extra) for mid, extra in emails])


def _promo(sender=SHOP, days=1, **extra):
    return {"sender": sender, "category": "Promotional", "date": _now(days=-days), **extra}


def test_lists_bulk_senders_busiest_first(auth_client, db, account):
    other = "News <hi@news.org>"
    _seed(
        db,
        account,
        ("s1", _promo(subject="Sale!", unsubscribe_url="https://shop.com/u?1", unsubscribe_one_click=True)),
        ("s2", _promo(days=2, unsubscribe_url="https://shop.com/u?old")),
        ("s3", _promo(days=3)),
        ("n1", _promo(sender=other, days=1)),
        ("n2", _promo(sender=other, days=2)),
        ("lone", _promo(sender="One <one@x.com>")),  # only one email: not a pattern yet
        ("real", {"sender": "Boss <boss@work.com>", "category": "Important"}),
        ("old", _promo(sender="Old <old@x.com>", days=60)),
        ("old2", _promo(sender="Old <old@x.com>", days=61)),
    )
    groups = auth_client.get("/api/newsletters").json()

    assert [(g["sender_address"], g["count"]) for g in groups] == [("deals@shop.com", 3), ("hi@news.org", 2)]
    shop = groups[0]
    assert shop["can_unsubscribe"] is True and shop["one_click"] is True  # the newest link decides
    assert shop["last_subject"] == "Sale!" and shop["muted"] is False
    assert groups[1]["can_unsubscribe"] is False
    assert [g["sender_address"] for g in auth_client.get("/api/newsletters", params={"days": 90}).json()][
        -1
    ] == "old@x.com"
    assert (
        len(auth_client.get("/api/newsletters", params={"min_count": 1}).json()) == 3
    )  # adds the single-mail sender


def test_a_non_promotional_sender_with_an_unsubscribe_link_counts_as_bulk(auth_client, db, account):
    link = {"unsubscribe_url": "https://digest.com/u", "category": "General"}
    _seed(
        db,
        account,
        ("a", {"sender": "Digest <d@digest.com>", "date": _now(days=-1), **link}),
        ("b", {"sender": "Digest <d@digest.com>", "date": _now(days=-2), **link}),
    )
    assert [g["sender_address"] for g in auth_client.get("/api/newsletters").json()] == ["d@digest.com"]


def test_one_click_unsubscribe_and_mute(auth_client, db, account):
    _seed(
        db,
        account,
        ("a", _promo(unsubscribe_url="https://shop.com/u", unsubscribe_one_click=True)),
        ("b", _promo(days=2)),
    )
    with patch(ONE_CLICK, return_value=True) as one_click:
        r = auth_client.post("/api/newsletters/unsubscribe", json={"sender_address": "Deals@Shop.com"})

    assert r.json() == {
        "method": "one_click",
        "url": None,
        "ok": True,
        "detail": "Unsubscribed.",
        "muted": True,
    }
    one_click.assert_called_once_with("https://shop.com/u")
    rules = auth_client.get("/api/rules").json()
    assert [(x["kind"], x["pattern"], x["category"]) for x in rules] == [
        ("sender", "deals@shop.com", "Promotional")
    ]
    assert auth_client.get("/api/newsletters").json()[0]["muted"] is True


def test_without_one_click_the_link_is_handed_back_to_open(auth_client, db, account):
    _seed(db, account, ("a", _promo(unsubscribe_url="https://shop.com/u")), ("b", _promo(days=2)))
    with patch(ONE_CLICK) as one_click:
        r = auth_client.post(
            "/api/newsletters/unsubscribe", json={"sender_address": "deals@shop.com", "mute": False}
        )
    body = r.json()
    assert (
        body["method"] == "link"
        and body["url"] == "https://shop.com/u"
        and body["ok"] is True
        and body["muted"] is False
    )
    one_click.assert_not_called()
    assert auth_client.get("/api/rules").json() == []


def test_mailto_links_are_handed_back_too(auth_client, db, account):
    _seed(
        db,
        account,
        ("a", _promo(unsubscribe_url="mailto:unsub@shop.com?subject=stop")),
        ("b", _promo(days=2)),
    )
    body = auth_client.post("/api/newsletters/unsubscribe", json={"sender_address": "deals@shop.com"}).json()
    assert body["method"] == "link" and body["url"].startswith("mailto:")


def test_no_unsubscribe_link_still_lets_you_mute(auth_client, db, account):
    _seed(db, account, ("a", _promo()), ("b", _promo(days=2)))
    body = auth_client.post("/api/newsletters/unsubscribe", json={"sender_address": "deals@shop.com"}).json()
    assert body["method"] == "none" and body["ok"] is False and body["muted"] is True


def test_failed_one_click_falls_back_to_the_link(auth_client, db, account):
    _seed(
        db,
        account,
        ("a", _promo(unsubscribe_url="https://shop.com/u", unsubscribe_one_click=True)),
        ("b", _promo(days=2)),
    )
    for outcome in (False, requests.ConnectionError("down")):
        kwargs = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
        with patch(ONE_CLICK, **kwargs):
            body = auth_client.post(
                "/api/newsletters/unsubscribe", json={"sender_address": "deals@shop.com", "mute": False}
            ).json()
        assert body["method"] == "link" and body["url"] == "https://shop.com/u" and body["ok"] is True


def test_unsafe_links_are_not_contacted_and_the_user_is_told(auth_client, db, account):
    _seed(
        db,
        account,
        ("a", _promo(unsubscribe_url="https://evil.example/u", unsubscribe_one_click=True)),
        ("b", _promo(days=2)),
    )
    with patch(ONE_CLICK, side_effect=UnsafeUrl("Refusing to contact a private or internal address")):
        body = auth_client.post(
            "/api/newsletters/unsubscribe", json={"sender_address": "deals@shop.com", "mute": False}
        ).json()
    assert (
        body["method"] == "none"
        and body["ok"] is False
        and "not safe" in body["detail"]
        and body["url"] is None
    )


def test_muting_reclassifies_the_senders_mail(auth_client, db, account):
    _seed(
        db,
        account,
        (
            "a",
            {
                "sender": SHOP,
                "category": "General",
                "unsubscribe_url": "https://shop.com/u",
                "date": _now(days=-1),
            },
        ),
        (
            "b",
            {
                "sender": SHOP,
                "category": "General",
                "unsubscribe_url": "https://shop.com/u",
                "date": _now(days=-2),
            },
        ),
    )
    auth_client.post("/api/newsletters/unsubscribe", json={"sender_address": "deals@shop.com"})
    assert auth_client.get(f"/api/emails/{account.id}:a").json()["category"] == "Promotional"


def test_unknown_sender_is_404_and_users_are_isolated(auth_client, bob_client, db, account, bob_account):
    _seed(db, account, ("a", _promo(unsubscribe_url="https://shop.com/u")), ("b", _promo(days=2)))
    assert (
        auth_client.post("/api/newsletters/unsubscribe", json={"sender_address": "nobody@x.com"}).status_code
        == 404
    )
    assert (
        bob_client.post("/api/newsletters/unsubscribe", json={"sender_address": "deals@shop.com"}).status_code
        == 404
    )
    assert bob_client.get("/api/newsletters").json() == []

from datetime import datetime, timedelta, timezone

from app.db.models import Activity
from app.repositories import emails as repo
from tests.conftest import stored_email

URGENT = "Urgent / Action Required"


def _now(**delta):
    return datetime.now(timezone.utc) + timedelta(**delta)


def _seed(db, account, *emails):
    repo.upsert_many(db, [stored_email(account, *e[:1], **e[1]) for e in emails])


def _ids(response):
    return [item["id"].split(":")[1] for item in response.json()["items"]]


# --- browsing and filters ------------------------------------------------------------------------


def test_lists_all_mail_newest_first_with_a_snippet(auth_client, db, account):
    _seed(
        db,
        account,
        ("old", {"date": _now(days=-5), "body": "x" * 500}),
        ("new", {"date": _now(hours=-1), "body": "  hello \n\n  world  "}),
    )
    data = auth_client.get("/api/inbox").json()
    assert data["total"] == 2 and [i["id"].split(":")[1] for i in data["items"]] == ["new", "old"]
    assert data["items"][0]["snippet"] == "hello world"
    assert len(data["items"][1]["snippet"]) == 160
    assert "body" not in data["items"][0]  # list rows stay light


def test_filters_by_category_mailbox_sender_and_age(auth_client, db, user, account):
    from tests.conftest import make_account

    other = make_account(db, user, "second@gmail.com")
    _seed(db, account, ("a", {"category": URGENT}), ("b", {"category": "Promotional", "sender": "Shop <s@shop.com>"}))
    repo.upsert_many(db, [stored_email(other, "c", date=_now(days=-20))])

    assert _ids(auth_client.get("/api/inbox", params={"category": URGENT})) == ["a"]
    assert _ids(auth_client.get("/api/inbox", params={"sender_address": "S@Shop.com"})) == ["b"]
    assert _ids(auth_client.get("/api/inbox", params={"account_id": other.id})) == ["c"]
    assert set(_ids(auth_client.get("/api/inbox", params={"days": 7}))) == {"a", "b"}
    assert auth_client.get("/api/inbox", params={"category": "Nonsense"}).status_code == 422


def test_keyword_search_matches_all_words_in_subject_sender_or_body(auth_client, db, account):
    _seed(
        db,
        account,
        ("invoice", {"subject": "Blue couch invoice", "body": "Thanks for your order"}),
        ("couch", {"subject": "Couch delivery", "body": "arrives Tuesday"}),
        ("sender", {"sender": "Couch World <hi@couchworld.com>", "subject": "Hello"}),
        ("other", {"subject": "Lunch", "body": "nothing relevant"}),
    )
    assert set(_ids(auth_client.get("/api/inbox", params={"q": "couch"}))) == {"invoice", "couch", "sender"}
    assert _ids(auth_client.get("/api/inbox", params={"q": "couch invoice"})) == ["invoice"]  # every word
    assert _ids(auth_client.get("/api/inbox", params={"q": "COUCH   tuesday"})) == ["couch"]  # any case, any spacing
    assert _ids(auth_client.get("/api/inbox", params={"q": "zebra"})) == []


def test_search_treats_wildcards_literally(auth_client, db, account):
    _seed(db, account, ("pct", {"subject": "50% off"}), ("plain", {"subject": "500 items"}), ("und", {"subject": "a_b"}))
    assert _ids(auth_client.get("/api/inbox", params={"q": "50%"})) == ["pct"]
    assert _ids(auth_client.get("/api/inbox", params={"q": "a_b"})) == ["und"]
    assert _ids(auth_client.get("/api/inbox", params={"q": "%"})) == ["pct"]


def test_pagination(auth_client, db, account):
    _seed(db, account, *[(f"m{i}", {"date": _now(minutes=-i)}) for i in range(5)])
    page1 = auth_client.get("/api/inbox", params={"limit": 2}).json()
    page3 = auth_client.get("/api/inbox", params={"limit": 2, "offset": 4}).json()
    assert page1["total"] == 5 and [i["id"].split(":")[1] for i in page1["items"]] == ["m0", "m1"]
    assert [i["id"].split(":")[1] for i in page3["items"]] == ["m4"]
    assert auth_client.get("/api/inbox", params={"limit": 500}).status_code == 422
    assert auth_client.get("/api/inbox", params={"offset": -1}).status_code == 422


def test_users_only_see_their_own_mail_and_mailboxes(auth_client, bob_client, db, account, bob_account):
    _seed(db, account, ("alices", {"subject": "secret plans"}))
    repo.upsert_many(db, [stored_email(bob_account, "bobs", subject="secret plans")])
    assert _ids(auth_client.get("/api/inbox", params={"q": "secret"})) == ["alices"]
    assert _ids(bob_client.get("/api/inbox", params={"q": "secret"})) == ["bobs"]
    assert auth_client.get("/api/inbox", params={"account_id": bob_account.id}).status_code == 404


def test_email_detail_is_private(auth_client, bob_client, db, account):
    _seed(db, account, ("m1", {"subject": "Hello", "body": "the full body"}))
    email_id = f"{account.id}:m1"
    detail = auth_client.get(f"/api/emails/{email_id}").json()
    assert detail["body"] == "the full body" and detail["reason"] is None and detail["category_source"] == "auto"
    assert bob_client.get(f"/api/emails/{email_id}").status_code == 404
    assert auth_client.get("/api/emails/9:nope").status_code == 404


def test_dates_are_always_returned_as_utc(auth_client, db, account):
    _seed(db, account, ("m1", {}))
    assert auth_client.get("/api/inbox").json()["items"][0]["date"].endswith("Z")
    assert auth_client.get(f"/api/emails/{account.id}:m1").json()["date"].endswith("Z")


# --- corrections ---------------------------------------------------------------------------------


def test_correcting_a_category_records_who_decided_and_why(auth_client, db, account):
    _seed(db, account, ("m1", {"category": "General", "score": 2.2}))
    r = auth_client.patch(f"/api/emails/{account.id}:m1", json={"category": "Important"})
    body = r.json()
    assert r.status_code == 200 and body["category"] == "Important" and body["category_source"] == "user"
    assert body["reason"] == "You set this to Important." and body["score"] == 3.3
    assert body["task"] is not None


def test_correcting_to_important_creates_a_calendar_item_and_away_from_it_removes_it(auth_client, db, account):
    _seed(db, account, ("m1", {"category": "General", "subject": "Report due", "body": "Please review the report."}))
    url = f"/api/emails/{account.id}:m1"

    auth_client.patch(url, json={"category": URGENT})
    activities = db.query(Activity).all()
    assert [a.email_id for a in activities] == [f"{account.id}:m1"]

    auth_client.patch(url, json={"category": "Promotional"})
    db.expire_all()
    assert db.query(Activity).count() == 0
    assert auth_client.get(url).json()["task"] is None


def test_correcting_away_keeps_activities_you_already_finished(auth_client, db, account):
    _seed(db, account, ("m1", {"category": URGENT}))
    url = f"/api/emails/{account.id}:m1"
    auth_client.patch(url, json={"category": URGENT})  # ensure the activity exists
    activity = db.query(Activity).one()
    auth_client.patch(f"/api/activities/{activity.id}", json={"status": "done"})

    auth_client.patch(url, json={"category": "General"})
    db.expire_all()
    assert db.query(Activity).count() == 1


def test_apply_to_sender_makes_a_rule_and_reclassifies_their_other_mail(auth_client, db, account):
    shop = "Shop <deals@shop.com>"
    _seed(
        db,
        account,
        ("a", {"sender": shop, "category": "General"}),
        ("b", {"sender": shop, "category": "General"}),
        ("mine", {"sender": shop, "category": "Important", "category_source": "user"}),
        ("elsewhere", {"sender": "Bob <bob@x.com>", "category": "General"}),
    )
    r = auth_client.patch(f"/api/emails/{account.id}:a", json={"category": "Promotional", "apply_to_sender": True})
    assert r.status_code == 200

    def category(mid):
        return auth_client.get(f"/api/emails/{account.id}:{mid}").json()

    assert category("a")["category_source"] == "user"
    assert category("b")["category"] == "Promotional" and category("b")["category_source"] == "rule"
    assert "deals@shop.com" in category("b")["reason"]
    assert category("mine")["category"] == "Important"  # a decision you made by hand is never overridden
    assert category("elsewhere")["category"] == "General"
    rules = auth_client.get("/api/rules").json()
    assert [(r["kind"], r["pattern"], r["category"]) for r in rules] == [("sender", "deals@shop.com", "Promotional")]


# --- done and snooze -----------------------------------------------------------------------------


def test_done_mail_leaves_the_open_list_and_completes_its_calendar_item(auth_client, db, account):
    _seed(db, account, ("m1", {"category": URGENT}), ("m2", {}))
    url = f"/api/emails/{account.id}:m1"
    auth_client.patch(url, json={"category": URGENT})  # creates the activity
    activity = db.query(Activity).one()

    assert auth_client.patch(url, json={"is_done": True}).json()["is_done"] is True
    assert _ids(auth_client.get("/api/inbox")) == ["m2"]
    assert _ids(auth_client.get("/api/inbox", params={"state": "done"})) == ["m1"]
    assert set(_ids(auth_client.get("/api/inbox", params={"state": "all"}))) == {"m1", "m2"}
    db.expire_all()
    assert db.get(Activity, activity.id).status == "done"

    auth_client.patch(url, json={"is_done": False})
    db.expire_all()
    assert set(_ids(auth_client.get("/api/inbox"))) == {"m1", "m2"} and db.get(Activity, activity.id).status == "todo"


def test_snoozed_mail_hides_until_its_time_then_returns(auth_client, db, account):
    _seed(db, account, ("m1", {}), ("m2", {}))
    url = f"/api/emails/{account.id}:m1"
    later = (_now(hours=3)).isoformat()

    assert auth_client.patch(url, json={"snoozed_until": later}).status_code == 200
    assert _ids(auth_client.get("/api/inbox")) == ["m2"]
    assert _ids(auth_client.get("/api/inbox", params={"state": "snoozed"})) == ["m1"]

    auth_client.patch(url, json={"snoozed_until": (_now(minutes=-1)).isoformat()})  # already elapsed
    assert set(_ids(auth_client.get("/api/inbox"))) == {"m1", "m2"}

    auth_client.patch(url, json={"snoozed_until": later})
    auth_client.patch(url, json={"snoozed_until": None})  # wake it now
    assert set(_ids(auth_client.get("/api/inbox"))) == {"m1", "m2"}


def test_done_and_snoozed_mail_are_not_action_items(auth_client, db, account):
    _seed(
        db,
        account,
        ("open", {"category": URGENT, "subject": "Task", "body": "please finish this asap"}),
        ("done", {"category": URGENT, "subject": "Task2", "body": "please finish this asap"}),
        ("snoozed", {"category": URGENT, "subject": "Task3", "body": "please finish this asap"}),
    )
    auth_client.patch(f"/api/emails/{account.id}:done", json={"is_done": True})
    auth_client.patch(f"/api/emails/{account.id}:snoozed", json={"snoozed_until": _now(hours=2).isoformat()})
    assert [t["id"].split(":")[1] for t in auth_client.get("/api/scheduler").json()] == ["open"]


def test_cannot_act_on_someone_elses_mail(auth_client, bob_client, db, account):
    _seed(db, account, ("m1", {}))
    r = bob_client.patch(f"/api/emails/{account.id}:m1", json={"is_done": True})
    assert r.status_code == 404
    assert auth_client.get(f"/api/emails/{account.id}:m1").json()["is_done"] is False


def test_invalid_updates_are_rejected(auth_client, db, account):
    _seed(db, account, ("m1", {}))
    url = f"/api/emails/{account.id}:m1"
    assert auth_client.patch(url, json={"category": "Super Urgent"}).status_code == 422
    assert auth_client.patch(url, json={"snoozed_until": "not a date"}).status_code == 422

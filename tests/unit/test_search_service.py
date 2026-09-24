from datetime import date, datetime, timezone
from unittest.mock import patch

import numpy as np

from app.repositories import emails as repo
from app.services import embeddings, search_service
from app.services.search_service import QueryPlan, fallback_plan, keyword_score, make_plan
from tests.conftest import stored_email

TODAY = date(2026, 9, 24)


def _vec(*values):
    v = np.zeros(embeddings.DIMENSIONS, dtype=np.float32)
    v[: len(values)] = values
    return embeddings.pack(embeddings.normalise(v))


def _ai_plan(**fields):
    return {"keywords": [], "date_from": None, "date_to": None, "category": None, "sender": None, **fields}


def _search(db, user_id, query, plan=None, query_vector=None, **kwargs):
    """Runs a search with the AI planning and embedding steps replaced by the given values."""
    with (
        patch.object(search_service.ai_summarizer, "generate_json", return_value=plan) if plan is not None
        else patch.object(search_service.ai_summarizer, "generate_json", side_effect=RuntimeError("no AI")),
        patch.object(search_service.embeddings, "embed_query", return_value=query_vector),
    ):
        return search_service.search(db, user_id, query, today=TODAY, **kwargs)


def _ids(result):
    return [e.id.split(":")[1] for e, _ in result.hits]


# --- planning ------------------------------------------------------------------------------------


def test_fallback_plan_drops_filler_words():
    plan = fallback_plan("Find the invoice for the blue couch")
    assert plan.keywords == ["invoice", "blue", "couch"] and not plan.used_ai


def test_make_plan_reads_the_models_answer():
    raw = _ai_plan(keywords=["Couch ", "invoice"], date_from="2026-06-01", date_to="2026-08-31", category="Important", sender="Ikea")
    with patch.object(search_service.ai_summarizer, "generate_json", return_value=raw):
        plan = make_plan("invoice for the blue couch last summer", TODAY)
    assert plan.keywords == ["couch", "invoice"] and plan.used_ai
    assert (plan.date_from, plan.date_to) == (date(2026, 6, 1), date(2026, 8, 31))
    assert plan.category == "Important" and plan.sender == "Ikea"


def test_make_plan_tells_the_model_todays_date_and_the_valid_categories():
    with patch.object(search_service.ai_summarizer, "generate_json", return_value=_ai_plan(keywords=["x"])) as gen:
        make_plan("something", TODAY)
    prompt = gen.call_args.args[0]
    assert "2026-09-24" in prompt and "Urgent / Action Required" in prompt and "something" in prompt


def test_make_plan_ignores_nonsense_from_the_model():
    raw = _ai_plan(keywords=["couch"], date_from="last summer", date_to="2026-13-45", category="Very Important")
    with patch.object(search_service.ai_summarizer, "generate_json", return_value=raw):
        plan = make_plan("couch", TODAY)
    assert plan.date_from is None and plan.date_to is None and plan.category is None and plan.keywords == ["couch"]


def test_make_plan_falls_back_when_the_ai_fails_or_returns_nothing_useful():
    with patch.object(search_service.ai_summarizer, "generate_json", side_effect=RuntimeError("down")):
        plan = make_plan("blue couch", TODAY)
    assert plan.keywords == ["blue", "couch"] and not plan.used_ai

    with patch.object(search_service.ai_summarizer, "generate_json", return_value=_ai_plan()):
        assert make_plan("blue couch", TODAY).keywords == ["blue", "couch"]  # empty keywords -> use the words

    with patch.object(search_service.ai_summarizer, "generate_json", return_value="not a dict"):
        assert not make_plan("blue couch", TODAY).used_ai


def test_plan_explanation_is_human_readable():
    plan = QueryPlan(["couch"], date(2026, 6, 1), date(2026, 8, 31), "Important", "ikea", True)
    text = plan.explain()
    assert "couch" in text and "from ikea" in text and "Important" in text and "2026-06-01" in text and "2026-08-31" in text
    assert QueryPlan([]).explain() == "Looking for mail matching your words"


# --- scoring -------------------------------------------------------------------------------------


def test_keyword_score_weights_subject_over_sender_over_body(account):
    def mail(**f):
        return type("E", (), {"subject": "", "sender": "", "body": "", **f})

    assert keyword_score(mail(subject="Couch order"), ["couch"]) == 1.0
    assert keyword_score(mail(sender="Couch World"), ["couch"]) == 2 / 3
    assert keyword_score(mail(body="the couch"), ["couch"]) == 1 / 3
    assert keyword_score(mail(subject="Couch", body="invoice"), ["couch", "invoice"]) == (3 + 1) / 6
    assert keyword_score(mail(subject="Lunch"), ["couch"]) == 0.0
    assert keyword_score(mail(subject="Couch"), []) == 0.0


# --- searching -----------------------------------------------------------------------------------


def test_keyword_only_search_ranks_better_matches_first(db, account):
    repo.upsert_many(
        db,
        [
            stored_email(account, "both", subject="Couch invoice", body="thanks"),
            stored_email(account, "partly", subject="Couch order", sender="Invoices Team <inv@x.com>", body="thanks"),
            stored_email(account, "none", subject="Lunch", body="nothing"),
        ],
    )
    result = _search(db, account.user_id, "couch invoice", plan=_ai_plan(keywords=["couch", "invoice"]))
    assert _ids(result) == ["both", "partly"] and result.semantic is False


def test_hits_far_below_the_best_one_are_dropped(db, account):
    repo.upsert_many(
        db,
        [
            stored_email(account, "strong", subject="Couch invoice"),
            stored_email(account, "weak", subject="Lunch", body="we talked about a couch once"),
        ],
    )
    result = _search(db, account.user_id, "couch", plan=_ai_plan(keywords=["couch"]))
    assert _ids(result) == ["strong"]  # the body-only mention scores a third of the best hit


def test_date_range_from_the_question_limits_the_candidates(db, account):
    def at(day):
        return datetime(2026, 7, day, tzinfo=timezone.utc)

    repo.upsert_many(
        db,
        [
            stored_email(account, "summer", subject="Couch invoice", date=at(10)),
            stored_email(account, "spring", subject="Couch invoice", date=datetime(2026, 4, 1, tzinfo=timezone.utc)),
            stored_email(account, "last-day", subject="Couch invoice", date=datetime(2026, 8, 31, 23, 0, tzinfo=timezone.utc)),
            stored_email(account, "after", subject="Couch invoice", date=datetime(2026, 9, 1, tzinfo=timezone.utc)),
        ],
    )
    plan = _ai_plan(keywords=["couch"], date_from="2026-06-01", date_to="2026-08-31")
    assert set(_ids(_search(db, account.user_id, "couch last summer", plan=plan))) == {"summer", "last-day"}


def test_sender_and_category_from_the_question_filter_too(db, account):
    repo.upsert_many(
        db,
        [
            stored_email(account, "ikea", sender="IKEA <hi@ikea.com>", subject="Order", category="Important"),
            stored_email(account, "other", sender="Bob <bob@x.com>", subject="Order", category="Important"),
            stored_email(account, "promo", sender="IKEA <hi@ikea.com>", subject="Order", category="Promotional"),
        ],
    )
    plan = _ai_plan(keywords=["order"], sender="ikea", category="Important")
    assert _ids(_search(db, account.user_id, "ikea order", plan=plan)) == ["ikea"]


def test_meaning_finds_mail_that_shares_no_words_with_the_question(db, account):
    repo.upsert_many(
        db,
        [
            stored_email(account, "sofa", subject="Your sofa receipt", embedding=_vec(1.0, 0.1)),
            stored_email(account, "lunch", subject="Lunch plans", embedding=_vec(0.0, 1.0)),
        ],
    )
    result = _search(db, account.user_id, "couch invoice", plan=_ai_plan(keywords=["couch"]), query_vector=embeddings.unpack(_vec(1.0, 0.0)))
    assert _ids(result) == ["sofa"] and result.semantic is True  # "sofa" never contains the word "couch"


def test_words_and_meaning_together_beat_either_alone(db, account):
    repo.upsert_many(
        db,
        [
            stored_email(account, "both", subject="Couch invoice", embedding=_vec(1.0, 0.2)),
            stored_email(account, "meaning", subject="Sofa receipt", embedding=_vec(1.0, 0.3)),
            stored_email(account, "words", subject="Couch cushions", embedding=_vec(0.3, 1.0)),
        ],
    )
    result = _search(db, account.user_id, "couch", plan=_ai_plan(keywords=["couch"]), query_vector=embeddings.unpack(_vec(1.0, 0.0)))
    assert _ids(result) == ["both"]  # matches on words AND meaning; the others match only one and fall below the cutoff


def test_emails_without_vectors_are_still_found_by_words(db, account):
    repo.upsert_many(db, [stored_email(account, "novec", subject="Couch invoice"), stored_email(account, "other", subject="Lunch")])
    result = _search(db, account.user_id, "couch", plan=_ai_plan(keywords=["couch"]), query_vector=embeddings.unpack(_vec(1.0)))
    assert _ids(result) == ["novec"] and result.semantic is False  # nothing has a vector yet: words only


def test_mail_with_a_vector_outranks_mail_without_one_when_both_match(db, account):
    repo.upsert_many(
        db,
        [
            stored_email(account, "novec", subject="Couch invoice"),
            stored_email(account, "vec", subject="Couch invoice", embedding=_vec(1.0)),
        ],
    )
    result = _search(db, account.user_id, "couch", plan=_ai_plan(keywords=["couch"]), query_vector=embeddings.unpack(_vec(1.0)))
    assert _ids(result)[0] == "vec"


def test_searches_only_the_users_own_mail_including_handled_mail(db, user, bob, account, bob_account):
    repo.upsert_many(db, [stored_email(account, "mine", subject="Couch invoice", is_done=True), stored_email(bob_account, "bobs", subject="Couch invoice")])
    assert _ids(_search(db, user.id, "couch", plan=_ai_plan(keywords=["couch"]))) == ["mine"]


def test_mailbox_filter_and_limit(db, user, account):
    from tests.conftest import make_account

    other = make_account(db, user, "second@gmail.com")
    repo.upsert_many(db, [stored_email(account, str(i), subject="Couch") for i in range(4)] + [stored_email(other, "x", subject="Couch")])
    plan = _ai_plan(keywords=["couch"])
    assert _ids(_search(db, user.id, "couch", plan=plan, account_id=other.id)) == ["x"]
    assert len(_search(db, user.id, "couch", plan=plan, limit=2).hits) == 2


def test_no_mail_or_no_match_is_an_empty_result_not_an_error(db, account):
    assert _search(db, account.user_id, "couch", plan=_ai_plan(keywords=["couch"])).hits == []
    repo.upsert_many(db, [stored_email(account, "a", subject="Lunch")])
    assert _search(db, account.user_id, "couch", plan=_ai_plan(keywords=["couch"])).hits == []


def test_works_end_to_end_without_any_ai(db, account):
    repo.upsert_many(db, [stored_email(account, "a", subject="Blue couch invoice"), stored_email(account, "b", subject="Lunch")])
    result = _search(db, account.user_id, "find the blue couch invoice")
    assert _ids(result) == ["a"] and result.plan.used_ai is False and result.semantic is False


def test_very_long_questions_are_truncated(db, account):
    repo.upsert_many(db, [stored_email(account, "a", subject="x")])  # something to rank, so the query is embedded
    with patch.object(search_service.ai_summarizer, "generate_json", return_value=_ai_plan(keywords=["x"])) as gen:
        with patch.object(search_service.embeddings, "embed_query", return_value=None) as embed:
            search_service.search(db, account.user_id, "y" * 2000, today=TODAY)
    assert len(embed.call_args.args[0]) == search_service.MAX_QUERY_CHARS
    assert "y" * (search_service.MAX_QUERY_CHARS + 1) not in gen.call_args.args[0]

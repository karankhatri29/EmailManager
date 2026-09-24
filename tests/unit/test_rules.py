import pytest

from app.services.rules import RuleError, RuleSpec, describe, match, normalize, to_specs

URGENT = "Urgent / Action Required"


@pytest.mark.parametrize(
    "kind,pattern,expected",
    [
        ("sender", "  News@Shop.COM ", "news@shop.com"),
        ("domain", "@Shop.com", "shop.com"),
        ("domain", "shop.com", "shop.com"),
        ("keyword", "  Black   Friday ", "black friday"),
    ],
)
def test_normalize_canonicalises(kind, pattern, expected):
    assert normalize(kind, pattern, URGENT) == RuleSpec(kind, expected, URGENT)


@pytest.mark.parametrize(
    "kind,pattern,category",
    [
        ("sender", "not-an-address", URGENT),
        ("sender", "@shop.com", URGENT),
        ("sender", "a b@shop.com", URGENT),
        ("domain", "localhost", URGENT),
        ("domain", "a@shop.com", URGENT),
        ("keyword", "ab", URGENT),
        ("keyword", "   ", URGENT),
        ("colour", "red", URGENT),
        ("sender", "a@b.com", "Very Important"),
    ],
)
def test_normalize_rejects_bad_rules(kind, pattern, category):
    with pytest.raises(RuleError):
        normalize(kind, pattern, category)


def test_sender_domain_and_keyword_matching():
    sender = RuleSpec("sender", "boss@work.com", URGENT)
    domain = RuleSpec("domain", "work.com", "Important")
    keyword = RuleSpec("keyword", "invoice", "Important")

    assert match([sender], "boss@work.com", "s", "b") == sender
    assert match([sender], "other@work.com", "s", "b") is None
    assert match([domain], "anyone@work.com", "s", "b") == domain
    assert match([domain], "anyone@mail.work.com", "s", "b") == domain  # subdomains count
    assert match([domain], "anyone@notwork.com", "s", "b") is None  # but lookalikes do not
    assert match([keyword], "x@y.com", "Your Invoice is ready", "") == keyword
    assert match([keyword], "x@y.com", "s", "see the invoices") is None  # whole words only
    assert match([keyword], "x@y.com", "s", "invoicing") is None


def test_most_specific_rule_wins_whatever_the_order():
    sender = RuleSpec("sender", "boss@work.com", URGENT)
    domain = RuleSpec("domain", "work.com", "Promotional")
    keyword = RuleSpec("keyword", "lunch", "General")
    for order in ([keyword, domain, sender], [sender, keyword, domain], [domain, sender, keyword]):
        assert match(order, "boss@work.com", "lunch?", "") == sender
    assert match([keyword, domain], "boss@work.com", "lunch?", "") == domain
    assert match([keyword], "a@b.com", "lunch?", "") == keyword


def test_no_rules_no_match():
    assert match([], "a@b.com", "s", "b") is None


def test_to_specs_and_describe():
    class Row:
        kind, pattern, category = "sender", "a@b.com", "Promotional"

    (spec,) = to_specs([Row()])
    assert spec == RuleSpec("sender", "a@b.com", "Promotional")
    assert describe(spec) == "Your rule: mail from a@b.com is always Promotional."
    assert "“invoice”" in describe(RuleSpec("keyword", "invoice", URGENT))

import pytest

from app.services.rules import RuleError, RuleSpec, describe, match, normalize


def hit(query, subject="", body="", address="a@b.com"):
    return match([RuleSpec("search", query, "Important")], address, subject, body) is not None


@pytest.mark.parametrize(
    "query,subject,body,expected",
    [
        ("invoice", "Your invoices are ready", "", True),  # substring, not whole word
        ("invoice", "Nothing here", "", False),
        ("invoice overdue", "Invoice", "it is OVERDUE", True),  # implicit AND, any case
        ("invoice overdue", "Invoice", "all fine", False),
        ("invoice OR receipt", "Your receipt", "", True),
        ("invoice | receipt", "Your receipt", "", True),
        ("invoice OR receipt", "Hello", "", False),
        ('"payment due"', "Payment, due today", "", True),  # phrase ignores punctuation
        ('"payment due"', "due payment", "", False),
        ("invoice -newsletter", "invoice", "weekly newsletter", False),
        ("invoice NOT newsletter", "invoice", "hello", True),
        ("(invoice OR receipt) AND paid", "receipt", "PAID", True),
        ("(invoice OR receipt) AND paid", "receipt", "unpaid soon", True),  # substring
        ("(invoice OR receipt) AND paid", "hello", "paid", False),
        ("subject:urgent", "URGENT: read", "", True),
        ("subject:urgent", "hi", "this is urgent", False),
        ("body:urgent", "hi", "this is urgent", True),
        ("=pay", "payment", "", False),  # whole word only
        ("=pay", "please pay now", "", True),
        ("inv*ce", "invoice", "", True),  # wildcard
        ("cafe", "Café menu", "", True),  # accents ignored
        ("cafe menu", "CAFÉ-MENU", "", True),
        ('"terms or conditions"', "Terms or Conditions", "", True),  # short words are fine inside a phrase
    ],
)
def test_search_semantics(query, subject, body, expected):
    assert hit(query, subject, body) is expected


def test_from_field_searches_the_sender():
    spec = [RuleSpec("search", "from:shop.com", "Important")]
    assert match(spec, "news@mail.shop.com", "", "") is not None
    assert match(spec, "news@other.com", "shop.com", "shop.com") is None  # only the address counts


@pytest.mark.parametrize(
    "query",
    ["-foo", "(abc", "abc)", '"abc', "ab", "abc OR", "OR abc", "a" * 3 + " " + " ".join(["bcd"] * 25)],
)
def test_bad_searches_are_rejected_with_a_reason(query):
    with pytest.raises(RuleError):
        normalize("search", query, "Important")


def test_normalize_keeps_operator_case_and_tidies_spaces():
    assert normalize("search", "  Invoice  OR  Receipt ", "Important") == RuleSpec(
        "search", "Invoice OR Receipt", "Important"
    )


def test_search_ranks_below_a_keyword_and_describes_itself():
    search = RuleSpec("search", "invoice", "Promotional")
    keyword = RuleSpec("keyword", "invoice", "Important")
    assert match([search, keyword], "a@b.com", "invoice", "") == keyword
    assert "search" in describe(search) and "invoice" in describe(search)

import pytest

from app.services.nlp_engine import (
    calculate_priority,
    process_text,
)


def test_process_text_empty():
    assert process_text("") == {"tokens": [], "entities": {}, "clean_text_raw": ""}


def test_process_text_strips_urls_and_lowercases():
    out = process_text("Visit https://example.com NOW")
    assert "http" not in out["clean_text_raw"]
    assert out["clean_text_raw"] == "visit now"


@pytest.mark.parametrize(
    "subject,body,category,low,high",
    [
        ("50% off sale", "shop now", "Promotional", 1.0, 1.4),
        ("Your OTP", "Your verification code is 123456", "Urgent / Action Required", 4.5, 4.8),
        ("Bank", "Rs 500 was debited from your account", "Urgent / Action Required", 4.2, 4.4),
        ("Assignment", "Please submit the report. Deadline is Friday", "Urgent / Action Required", 4.0, 4.1),
        ("Announcement", "A new schedule is available", "Important", 3.1, 3.5),
        ("Hi", "Lunch was great yesterday", "General", 2.0, 2.5),
    ],
)
def test_calculate_priority_categories(subject, body, category, low, high):
    score, cat = calculate_priority(subject, body)
    assert cat == category
    assert low <= score <= high


def test_informational_guard_blocks_urgent():
    _, cat = calculate_priority("Notice", "This is for information. No action required. Deadline noted.")
    assert cat == "Important"


def test_a_negated_deadline_is_not_a_deadline():
    _, cat = calculate_priority("Policy", "There is no deadline for this. Please read when you have time.")
    assert cat != "Urgent / Action Required"


def test_model_loads_from_the_package_when_its_metadata_is_missing(monkeypatch):
    """Serverless bundles can drop the .dist-info folder spacy.load(name) looks for; the package itself still loads."""
    from app.services import nlp_engine

    calls = []

    def no_metadata(name):
        calls.append(name)
        raise OSError("E050: Can't find model")

    monkeypatch.setattr(nlp_engine.spacy, "load", no_metadata)
    assert nlp_engine.load_model()("Please pay the bill.")[0].text == "Please"
    assert calls == ["en_core_web_sm"]


def test_a_missing_model_gives_a_clear_error_and_downloads_nothing(monkeypatch):
    import importlib

    from app.services import nlp_engine

    def missing(name, *args, **kwargs):
        raise ImportError(name)

    monkeypatch.setattr(nlp_engine.spacy, "load", lambda name: (_ for _ in ()).throw(OSError("E050")))
    monkeypatch.setattr(importlib, "import_module", missing)
    with pytest.raises(RuntimeError, match="not installed"):
        nlp_engine.load_model()

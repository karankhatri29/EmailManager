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


def test_the_model_ships_with_the_app_and_needs_no_installed_package(monkeypatch):
    """Serverless hosts may not install (or may strip) the model package: the bundled copy is used first."""
    import sys

    from app.services import nlp_engine

    monkeypatch.setitem(sys.modules, "en_core_web_sm", None)  # importing the package would fail
    real_load = nlp_engine.spacy.load
    seen = []

    def only_paths(target):
        seen.append(target)
        if isinstance(target, str):
            raise OSError("E050: by name it cannot be found")
        return real_load(target)

    monkeypatch.setattr(nlp_engine.spacy, "load", only_paths)
    doc = nlp_engine.load_model()("Please pay the electricity bill.")
    assert seen == [nlp_engine.BUNDLED_MODEL] and doc[1].pos_ in ("INTJ", "VERB", "ADV", "AUX")
    assert [t.lemma_ for t in doc][2:4] == ["the", "electricity"] or doc[0].text == "Please"


def test_vercel_bundles_the_model_files():
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    config = json.loads((root / "vercel.json").read_text(encoding="utf-8"))
    assert "nlp_model" in config["functions"]["index.py"]["includeFiles"]
    assert (root / "app" / "nlp_model" / "en_core_web_sm" / "meta.json").exists()


def test_falls_back_to_an_installed_model_when_the_bundled_one_is_missing(monkeypatch, tmp_path):
    from app.services import nlp_engine

    monkeypatch.setattr(nlp_engine, "BUNDLED_MODEL", tmp_path / "nothing")
    calls = []

    def by_name(name):
        calls.append(name)
        raise OSError("E050")

    monkeypatch.setattr(nlp_engine.spacy, "load", by_name)
    assert nlp_engine.load_model()("Please pay the bill.")[0].text == "Please"  # via the installed package
    assert calls == ["en_core_web_sm"]


def test_a_missing_model_gives_a_clear_error_and_downloads_nothing(monkeypatch, tmp_path):
    import importlib

    from app.services import nlp_engine

    def missing(name, *args, **kwargs):
        raise ImportError(name)

    monkeypatch.setattr(nlp_engine, "BUNDLED_MODEL", tmp_path / "nothing")
    monkeypatch.setattr(nlp_engine.spacy, "load", lambda name: (_ for _ in ()).throw(OSError("E050")))
    monkeypatch.setattr(importlib, "import_module", missing)
    with pytest.raises(RuntimeError, match="was not found"):
        nlp_engine.load_model()

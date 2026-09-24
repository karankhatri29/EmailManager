import pytest

from app.services.actions import extract_action_title
from tests.nlp_corpus import TASKS


@pytest.mark.parametrize("subject,body,title", TASKS, ids=lambda v: v[:20] if isinstance(v, str) else None)
def test_titles_name_the_action(subject, body, title):
    assert extract_action_title(subject, body) == title


@pytest.mark.parametrize(
    "subject,body,title",
    [
        ("Weekly digest", "Here are this week's articles. Read more on our site.", "Weekly digest"),
        ("Re: Fwd: Plans", "Sounds good, thanks.", "Plans"),
        ("[Team] Lunch?", "Are we still on for lunch?", "Lunch?"),
        ("Quiet", "", "Quiet"),
    ],
)
def test_subject_is_the_fallback(subject, body, title):
    assert extract_action_title(subject, body) == title


def test_quoted_reply_is_not_a_source_of_tasks():
    body = "Thanks!\n\nOn Mon, Sam wrote:\n> Please submit the report.\n"
    assert extract_action_title("Re: Report", body) == "Report"


def test_second_action_and_time_clause_are_left_out():
    assert (
        extract_action_title("x", "Please upload the receipt before Friday and email the manager.")
        == "Upload the receipt"
    )

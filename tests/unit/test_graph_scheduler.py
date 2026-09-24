from app.services.graph_scheduler import build_scheduler_graph
from tests.conftest import make_email

URGENT = "Urgent / Action Required"


def test_empty_input():
    assert build_scheduler_graph([]) == []


def test_promotional_and_general_excluded():
    emails = [make_email("1", category="Promotional"), make_email("2", category="General")]
    assert build_scheduler_graph(emails) == []


def test_ordering_by_tier_then_score():
    emails = [
        make_email("a", "Task", "no dates here", category="Important", score=3.3),
        make_email("b", "Task2", "please finish this asap", category=URGENT, score=4.0),
        make_email("c", "Task3", "no dates here either", category="Important", score=3.5),
    ]
    out = build_scheduler_graph(emails)
    assert [t["id"] for t in out] == ["b", "c", "a"]
    assert out[0]["sort_tier"] == 3 and out[1]["sort_tier"] == 4


def test_isolated_tier1_drops_to_tier2():
    e = make_email("x", "Solo", "submit by 15/06/2026", category=URGENT, score=4.0)
    assert build_scheduler_graph([e])[0]["sort_tier"] == 2


def test_clustered_project_keeps_tier1():
    emails = [
        make_email(str(i), "[Proj] Thing", "submit by 15/06/2026", category=URGENT, score=4.0)
        for i in range(2)
    ]
    assert all(t["sort_tier"] == 1 for t in build_scheduler_graph(emails))

from unittest.mock import patch

import numpy as np

from app.repositories import emails as repo
from app.services import embeddings
from tests.conftest import stored_email


def test_pack_roundtrip_is_compact():
    vector = [0.25, -0.5, 1.0]
    blob = embeddings.pack(vector)
    assert len(blob) == 12  # float32
    assert list(embeddings.unpack(blob)) == vector


def test_normalise_gives_unit_vectors_and_tolerates_zero():
    unit = embeddings.normalise(np.array([3.0, 4.0], dtype=np.float32))
    assert abs(float(np.linalg.norm(unit)) - 1.0) < 1e-6
    assert list(embeddings.normalise(np.zeros(3, dtype=np.float32))) == [0.0, 0.0, 0.0]


def test_email_text_is_capped():
    assert len(embeddings.email_text("s", "a@b.com", "x" * 5000)) == embeddings.MAX_TEXT_CHARS
    assert embeddings.email_text("Sub", "a@b.com", "body").startswith("Sub\nFrom: a@b.com")


def test_embed_query_returns_a_unit_vector():
    with patch.object(embeddings, "embed_texts", return_value=[[3.0, 4.0]]) as embed:
        vector = embeddings.embed_query("blue couch")
    assert abs(float(np.linalg.norm(vector)) - 1.0) < 1e-6
    assert embed.call_args.args[1] == "RETRIEVAL_QUERY"


def test_embed_query_failure_means_no_vector_not_an_error():
    with patch.object(embeddings, "embed_texts", side_effect=RuntimeError("quota")):
        assert embeddings.embed_query("blue couch") is None


def _vectors(texts, task_type):
    return [[float(len(t)), 1.0] for t in texts]


def test_embed_pending_stores_normalised_vectors_in_batches(db, account):
    repo.upsert_many(db, [stored_email(account, str(i)) for i in range(5)])
    with patch.object(embeddings, "embed_texts", side_effect=_vectors) as embed, patch.object(embeddings, "BATCH_SIZE", 2):
        assert embeddings.embed_pending(db, account.id) == 5

    assert embed.call_count == 3  # 5 emails, batches of 2
    assert all(call.args[1] == "RETRIEVAL_DOCUMENT" for call in embed.call_args_list)
    rows = repo.list_in_window(db, account.user_id, 1)
    assert all(e.embedding is not None for e in rows)
    assert abs(float(np.linalg.norm(embeddings.unpack(rows[0].embedding))) - 1.0) < 1e-5
    assert repo.list_pending_embeddings(db, account.id, 100) == []


def test_embed_pending_only_does_what_is_missing(db, account):
    repo.upsert_many(db, [stored_email(account, "done", embedding=embeddings.pack([1.0, 0.0])), stored_email(account, "todo")])
    with patch.object(embeddings, "embed_texts", side_effect=_vectors) as embed:
        assert embeddings.embed_pending(db, account.id) == 1
    assert len(embed.call_args.args[0]) == 1


def test_a_failed_batch_is_left_for_the_next_sync(db, account):
    repo.upsert_many(db, [stored_email(account, str(i)) for i in range(3)])
    with patch.object(embeddings, "embed_texts", side_effect=RuntimeError("rate limited")):
        assert embeddings.embed_pending(db, account.id) == 0
    assert len(repo.list_pending_embeddings(db, account.id, 100)) == 3


def test_a_failure_part_way_keeps_the_batches_already_done(db, account):
    repo.upsert_many(db, [stored_email(account, str(i)) for i in range(4)])
    calls = iter([None, RuntimeError("boom")])

    def flaky(texts, task_type):
        outcome = next(calls)
        if outcome:
            raise outcome
        return _vectors(texts, task_type)

    with patch.object(embeddings, "embed_texts", side_effect=flaky), patch.object(embeddings, "BATCH_SIZE", 2):
        assert embeddings.embed_pending(db, account.id) == 2
    assert len(repo.list_pending_embeddings(db, account.id, 100)) == 2


def test_the_per_sync_limit_is_respected(db, account):
    repo.upsert_many(db, [stored_email(account, str(i)) for i in range(5)])
    with patch.object(embeddings, "embed_texts", side_effect=_vectors):
        assert embeddings.embed_pending(db, account.id, limit=3) == 3
    assert len(repo.list_pending_embeddings(db, account.id, 100)) == 2

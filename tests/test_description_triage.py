"""Unit tests for vlmd_description_triage: LLM suggestions for short descriptions.

Put tests here for triage() and its helpers. The LLM is replaced by
monkeypatching vlmd_description_triage.call_llm with canned replies, so these
tests cover what triage does with the model's output, not the prompt's quality.
"""

import json

import pytest

import vlmd_description_triage


def candidates(*names):
    return [{"name": n, "vlmd_field_draft": {"description": "n/a"}} for n in names]


def reply_with(monkeypatch, *texts):
    """Make call_llm return each text in turn, repeating the last; returns the prompts sent."""
    prompts = []

    def fake_call_llm(system, user, **kwargs):
        prompts.append(user)
        return texts[min(len(prompts), len(texts)) - 1], True, ""

    monkeypatch.setattr(vlmd_description_triage, "call_llm", fake_call_llm)
    return prompts


def entry(name, decision="leave_as_is"):
    return {"name": name, "decision": decision, "justification": "ok"}


# --- Well-formed replies ---
# Valid entries are returned as given; an unknown decision falls back to
# flag_for_human. A reply covering every field is checkpointed and not retried.


def test_valid_reply_is_returned(monkeypatch, tmp_path):
    prompts = reply_with(monkeypatch, json.dumps([entry("a"), entry("b", "maybe")]))
    checkpoint = tmp_path / "checkpoint.json"
    results = vlmd_description_triage.triage(
        candidates("a", "b"), "test-model", checkpoint_path=str(checkpoint)
    )
    assert results["a"]["decision"] == "leave_as_is"
    assert results["b"]["decision"] == "flag_for_human"
    assert not any(r.get("triage_failed") for r in results.values())
    assert len(prompts) == 1
    assert checkpoint.exists()


def test_entries_for_fields_not_in_the_chunk_are_dropped(monkeypatch, tmp_path):
    reply_with(monkeypatch, json.dumps([entry("a"), entry("b"), entry("zzz"), entry("A")]))
    checkpoint = tmp_path / "checkpoint.json"
    results = vlmd_description_triage.triage(
        candidates("a", "b"), "test-model", checkpoint_path=str(checkpoint)
    )
    assert set(results) == {"a", "b"}
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert {e["name"] for e in saved["0"]} == {"a", "b"}


# --- Malformed or incomplete replies ---
# A reply that doesn't cover every field is retried once. Fields still uncovered
# are flagged for a human with "triage_failed": True instead of aborting the
# pipeline, and the chunk isn't checkpointed, so a re-run asks again.


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        '{"name": "a"}',
        '["a", "b"]',
        "[]",
        '[{"decision": "leave_as_is"}]',
        '[{"name": ["a"], "decision": "leave_as_is"}]',
        '[{"name": {"a": 1}, "decision": "leave_as_is"}]',
        '[{"name": 1, "decision": "leave_as_is"}]',
        '[{"name": "zzz", "decision": "leave_as_is"}]',
    ],
)
def test_unusable_reply_flags_chunk_for_human(monkeypatch, tmp_path, text):
    prompts = reply_with(monkeypatch, text)
    checkpoint = tmp_path / "checkpoint.json"
    results = vlmd_description_triage.triage(
        candidates("a", "b"), "test-model", checkpoint_path=str(checkpoint)
    )
    assert set(results) == {"a", "b"}
    assert {r["decision"] for r in results.values()} == {"flag_for_human"}
    assert all(r["triage_failed"] for r in results.values())
    assert len(prompts) == 2
    assert not checkpoint.exists()


def test_retry_can_recover_a_malformed_reply(monkeypatch, tmp_path):
    prompts = reply_with(monkeypatch, "Sure! Here it is:", json.dumps([entry("a"), entry("b")]))
    checkpoint = tmp_path / "checkpoint.json"
    results = vlmd_description_triage.triage(
        candidates("a", "b"), "test-model", checkpoint_path=str(checkpoint)
    )
    assert {r["decision"] for r in results.values()} == {"leave_as_is"}
    assert "IMPORTANT" in prompts[1]
    assert checkpoint.exists()


def test_partial_reply_keeps_usable_entries_and_is_not_checkpointed(monkeypatch, tmp_path):
    reply_with(monkeypatch, json.dumps(["junk", entry("a", "send_to_llm")]), "[]")
    checkpoint = tmp_path / "checkpoint.json"
    results = vlmd_description_triage.triage(
        candidates("a", "b"), "test-model", checkpoint_path=str(checkpoint)
    )
    assert results["a"] == entry("a", "send_to_llm")
    assert results["b"]["decision"] == "flag_for_human"
    assert results["b"]["triage_failed"] is True
    assert not checkpoint.exists()


def test_retry_fills_gaps_in_a_partial_reply(monkeypatch):
    reply_with(monkeypatch, json.dumps([entry("a", "send_to_llm")]), json.dumps([entry("b")]))
    results = vlmd_description_triage.triage(candidates("a", "b"), "test-model")
    assert results == {"a": entry("a", "send_to_llm"), "b": entry("b")}


def test_retry_asks_only_about_missing_fields(monkeypatch):
    prompts = reply_with(monkeypatch, json.dumps([entry("a")]), json.dumps([entry("b")]))
    vlmd_description_triage.triage(candidates("a", "b"), "test-model")
    assert '"name": "a"' in prompts[0] and '"name": "b"' in prompts[0]
    assert '"name": "a"' not in prompts[1] and '"name": "b"' in prompts[1]


def test_retry_does_not_replace_entries_from_the_first_reply(monkeypatch):
    # "maybe" would become flag_for_human and replace the valid send_to_llm.
    reply_with(
        monkeypatch,
        json.dumps([entry("a", "send_to_llm")]),
        json.dumps([entry("a", "maybe"), entry("b")]),
    )
    results = vlmd_description_triage.triage(candidates("a", "b"), "test-model")
    assert results == {"a": entry("a", "send_to_llm"), "b": entry("b")}


def test_retry_allows_a_longer_reply(monkeypatch):
    # A reply cut off at the token limit isn't valid JSON; the same limit would cut
    # off the retry too.
    limits = []

    def fake_call_llm(system, user, max_tokens, **kwargs):
        limits.append(max_tokens)
        return ('[{"name": "a", "decision": "leave_as_is", "justi', True, "")

    monkeypatch.setattr(vlmd_description_triage, "call_llm", fake_call_llm)
    vlmd_description_triage.triage(candidates("a"), "test-model")
    assert limits[1] > limits[0]


# --- Checkpoints ---
# A complete checkpointed chunk is reused without an LLM call; a partial one
# (as an older version of triage could write) is asked about again.


def write_checkpoint(path, chunk_entries, names=("a", "b")):
    fingerprint = "|".join(f"{n}:n/a" for n in names)
    path.write_text(json.dumps({"_fingerprint": fingerprint, "0": chunk_entries}), encoding="utf-8")


def test_complete_checkpoint_is_reused(monkeypatch, tmp_path):
    checkpoint = tmp_path / "checkpoint.json"
    write_checkpoint(checkpoint, [entry("a"), entry("b")])
    prompts = reply_with(monkeypatch, "[]")
    results = vlmd_description_triage.triage(
        candidates("a", "b"), "test-model", checkpoint_path=str(checkpoint)
    )
    assert results == {"a": entry("a"), "b": entry("b")}
    assert prompts == []


def test_partial_checkpoint_is_asked_again(monkeypatch, tmp_path):
    checkpoint = tmp_path / "checkpoint.json"
    write_checkpoint(checkpoint, [entry("a")])
    prompts = reply_with(monkeypatch, json.dumps([entry("a"), entry("b", "send_to_llm")]))
    results = vlmd_description_triage.triage(
        candidates("a", "b"), "test-model", checkpoint_path=str(checkpoint)
    )
    assert results["b"]["decision"] == "send_to_llm"
    assert len(prompts) == 1


def test_checkpoint_with_duplicate_field_names_is_reused(monkeypatch, tmp_path):
    # vlmd_convert only warns about duplicate names, so two "a" fields can reach triage.
    checkpoint = tmp_path / "checkpoint.json"
    write_checkpoint(checkpoint, [entry("a"), entry("a")], names=("a", "a"))
    prompts = reply_with(monkeypatch, "[]")
    results = vlmd_description_triage.triage(
        candidates("a", "a"), "test-model", checkpoint_path=str(checkpoint)
    )
    assert results == {"a": entry("a")}
    assert prompts == []

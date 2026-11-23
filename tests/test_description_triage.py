"""Unit tests for vlmd_description_triage: LLM suggestions for short descriptions.

Put tests here for triage() and its helpers. The LLM is replaced by
monkeypatching vlmd_description_triage.call_llm with canned replies, so these
tests cover what triage does with the model's output, not the prompt's quality.
"""

import json

import pytest

import vlmd_description_triage
from vlmd_description_triage import Decision


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


def entry(name, decision=Decision.LEAVE_AS_IS):
    return {"name": name, "decision": decision, "justification": "ok"}


# --- Building prompts and parsing replies ---
# build_prompt() and parse_reply() on their own, with no LLM call.


def test_prompt_lists_the_fields_after_the_base_prompt_and_ends_with_the_reminder():
    items = [{"name": "a", "description": "é"}]
    prompt = vlmd_description_triage.build_prompt(items, "Fields to triage:", "\n\nREMINDER")
    assert prompt.startswith("Fields to triage:\n\n[")
    assert '"description": "é"' in prompt
    assert prompt.endswith("]\n\nREMINDER")


@pytest.mark.parametrize(
    "raw",
    [
        '[{"name": "a", "decision": "leave_as_is", "justification": "ok"}]',
        '```json\n[{"name": "a", "decision": "leave_as_is", "justification": "ok"}]\n```',
    ],
)
def test_reply_is_parsed_into_entries_by_name(raw):
    assert vlmd_description_triage.parse_reply(raw, {"a"}) == {"a": entry("a")}


@pytest.mark.parametrize(
    "raw",
    ["Sure! Here it is:", '{"name": "a"}', '[{"name": "zzz", "decision": "leave_as_is"}]'],
)
def test_unusable_reply_parses_to_nothing(raw):
    assert vlmd_description_triage.parse_reply(raw, {"a"}) == {}


def test_parsed_reply_with_unknown_decision_is_flagged_for_human():
    parsed = vlmd_description_triage.parse_reply('[{"name": "a", "decision": "maybe"}]', {"a"})
    assert parsed["a"]["decision"] == Decision.FLAG_FOR_HUMAN


# --- Well-formed replies ---
# Valid entries are returned as given; an unknown decision falls back to
# flag_for_human. A reply covering every field is checkpointed and not retried.


def test_valid_reply_is_returned(monkeypatch, tmp_path):
    prompts = reply_with(monkeypatch, json.dumps([entry("a"), entry("b", "maybe")]))
    checkpoint = tmp_path / "checkpoint.json"
    results = vlmd_description_triage.triage(
        candidates("a", "b"), "test-model", checkpoint_path=str(checkpoint)
    )
    assert results["a"]["decision"] == Decision.LEAVE_AS_IS
    assert results["b"]["decision"] == Decision.FLAG_FOR_HUMAN
    assert not any(r.get("triage_failed") for r in results.values())
    assert len(prompts) == 1
    assert checkpoint.exists()


@pytest.mark.parametrize("decision", ["maybe", "", None, ["leave_as_is"], {"x": 1}, 1])
def test_unknown_decision_becomes_flag_for_human(monkeypatch, decision):
    reply_with(monkeypatch, json.dumps([{"name": "a", "decision": decision}]))
    results = vlmd_description_triage.triage(candidates("a"), "test-model")
    assert results["a"]["decision"] == Decision.FLAG_FOR_HUMAN
    assert not results["a"].get("triage_failed")


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
# pipeline, and the chunk isn't checkpointed, so a re-run asks again. A reply with
# no content counts as unusable; any other failed call stops triage.


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
    assert {r["decision"] for r in results.values()} == {Decision.FLAG_FOR_HUMAN}
    assert all(r["triage_failed"] for r in results.values())
    assert len(prompts) == 2
    assert not checkpoint.exists()


def test_retry_can_recover_a_malformed_reply(monkeypatch, tmp_path):
    prompts = reply_with(monkeypatch, "Sure! Here it is:", json.dumps([entry("a"), entry("b")]))
    checkpoint = tmp_path / "checkpoint.json"
    results = vlmd_description_triage.triage(
        candidates("a", "b"), "test-model", checkpoint_path=str(checkpoint)
    )
    assert {r["decision"] for r in results.values()} == {Decision.LEAVE_AS_IS}
    assert "IMPORTANT" in prompts[1]
    assert checkpoint.exists()


def test_partial_reply_keeps_usable_entries_and_is_not_checkpointed(monkeypatch, tmp_path):
    reply_with(monkeypatch, json.dumps(["junk", entry("a", Decision.SEND_TO_LLM)]), "[]")
    checkpoint = tmp_path / "checkpoint.json"
    results = vlmd_description_triage.triage(
        candidates("a", "b"), "test-model", checkpoint_path=str(checkpoint)
    )
    assert results["a"] == entry("a", Decision.SEND_TO_LLM)
    assert results["b"]["decision"] == Decision.FLAG_FOR_HUMAN
    assert results["b"]["triage_failed"] is True
    assert not checkpoint.exists()


def test_retry_fills_gaps_in_a_partial_reply(monkeypatch):
    reply_with(
        monkeypatch, json.dumps([entry("a", Decision.SEND_TO_LLM)]), json.dumps([entry("b")])
    )
    results = vlmd_description_triage.triage(candidates("a", "b"), "test-model")
    assert results == {"a": entry("a", Decision.SEND_TO_LLM), "b": entry("b")}


def test_retry_asks_only_about_missing_fields(monkeypatch):
    prompts = reply_with(monkeypatch, json.dumps([entry("a")]), json.dumps([entry("b")]))
    vlmd_description_triage.triage(candidates("a", "b"), "test-model")
    assert '"name": "a"' in prompts[0] and '"name": "b"' in prompts[0]
    assert '"name": "a"' not in prompts[1] and '"name": "b"' in prompts[1]


def test_retry_does_not_replace_entries_from_the_first_reply(monkeypatch):
    # "maybe" would become flag_for_human and replace the valid send_to_llm.
    reply_with(
        monkeypatch,
        json.dumps([entry("a", Decision.SEND_TO_LLM)]),
        json.dumps([entry("a", "maybe"), entry("b")]),
    )
    results = vlmd_description_triage.triage(candidates("a", "b"), "test-model")
    assert results == {"a": entry("a", Decision.SEND_TO_LLM), "b": entry("b")}


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


def call_results(monkeypatch, *results):
    """Make call_llm return each (text, ok, error) tuple in turn."""
    calls = iter(results)
    monkeypatch.setattr(vlmd_description_triage, "call_llm", lambda *a, **kw: next(calls))


def test_reply_with_no_content_is_treated_as_unusable(monkeypatch, tmp_path):
    from llm_client import NO_CONTENT_ERROR

    call_results(
        monkeypatch,
        (json.dumps([entry("a", Decision.SEND_TO_LLM)]), True, ""),
        ("", False, f"{NO_CONTENT_ERROR} (finish_reason=content_filter)"),
    )
    checkpoint = tmp_path / "checkpoint.json"
    results = vlmd_description_triage.triage(
        candidates("a", "b"), "test-model", checkpoint_path=str(checkpoint)
    )
    assert results["a"] == entry("a", Decision.SEND_TO_LLM)
    assert results["b"]["triage_failed"] is True
    assert not checkpoint.exists()


def test_failed_llm_call_stops_triage(monkeypatch):
    call_results(monkeypatch, ("", False, "Connection error."))
    with pytest.raises(RuntimeError, match="Connection error"):
        vlmd_description_triage.triage(candidates("a"), "test-model")


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
    prompts = reply_with(monkeypatch, json.dumps([entry("a"), entry("b", Decision.SEND_TO_LLM)]))
    results = vlmd_description_triage.triage(
        candidates("a", "b"), "test-model", checkpoint_path=str(checkpoint)
    )
    assert results["b"]["decision"] == Decision.SEND_TO_LLM
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

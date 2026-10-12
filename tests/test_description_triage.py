"""Unit tests for vlmd_description_triage: LLM suggestions for short descriptions.

Put tests here for triage() and its helpers. The LLM is replaced by
monkeypatching vlmd_description_triage.call_llm with a canned reply, so these
tests cover what triage does with the model's output, not the prompt's quality.
"""

import json

import pytest

import vlmd_description_triage


def candidates(*names):
    return [{"name": n, "vlmd_field_draft": {"description": "n/a"}} for n in names]


def reply_with(monkeypatch, text):
    monkeypatch.setattr(
        vlmd_description_triage, "call_llm", lambda *args, **kwargs: (text, True, "")
    )


# --- Well-formed replies ---
# Valid entries are returned as given; an unknown decision falls back to
# flag_for_human.


def test_valid_reply_is_returned(monkeypatch):
    reply_with(
        monkeypatch,
        json.dumps(
            [
                {"name": "a", "decision": "leave_as_is", "justification": "ok"},
                {"name": "b", "decision": "maybe", "justification": "?"},
            ]
        ),
    )
    results = vlmd_description_triage.triage(candidates("a", "b"), "test-model")
    assert results["a"]["decision"] == "leave_as_is"
    assert results["b"]["decision"] == "flag_for_human"


# --- Malformed replies ---
# Output triage can't use flags the chunk's fields for a human instead of
# aborting the pipeline, and isn't checkpointed, so a re-run tries again.


@pytest.mark.parametrize(
    "text",
    ["not json", '{"name": "a"}', '["a", "b"]', "[]", '[{"decision": "leave_as_is"}]'],
)
def test_malformed_reply_flags_chunk_for_human(monkeypatch, tmp_path, text):
    reply_with(monkeypatch, text)
    checkpoint = tmp_path / "checkpoint.json"
    results = vlmd_description_triage.triage(
        candidates("a", "b"), "test-model", checkpoint_path=str(checkpoint)
    )
    assert {r["decision"] for r in results.values()} == {"flag_for_human"}
    assert set(results) == {"a", "b"}
    assert not checkpoint.exists()


def test_unusable_entries_are_dropped(monkeypatch):
    reply_with(monkeypatch, json.dumps(["junk", {"name": "a", "decision": "send_to_llm"}]))
    results = vlmd_description_triage.triage(candidates("a", "b"), "test-model")
    # "b" has no suggestion; run_pipeline treats that as flag_for_human.
    assert results == {"a": {"name": "a", "decision": "send_to_llm"}}

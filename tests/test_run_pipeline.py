"""Unit tests for run_pipeline's helpers, called directly rather than through run().

Put tests here for a single helper in run_pipeline.py, such as the description
review gate, with its stage calls monkeypatched. End-to-end runs of run() go in
test_examples.py instead.
"""

import json

import run_pipeline
import vlmd_description_triage


def lint_record(name):
    return {
        "name": name,
        "issues": [run_pipeline.DESCRIPTION_REVIEW_ISSUE],
        "vlmd_field_draft": {"description": "n/a", "section": "s"},
    }


# --- Description review gate ---
# Triage suggestions become the review file and exit code 3.


def test_review_file_marks_fields_triage_could_not_handle(monkeypatch, tmp_path):
    suggestions = {
        "a": {"name": "a", "decision": "flag_for_human", "justification": "jargon"},
        "b": {
            "name": "b",
            "decision": "flag_for_human",
            "justification": "no usable suggestion",
            "triage_failed": True,
        },
    }
    monkeypatch.setattr(vlmd_description_triage, "triage", lambda *a, **k: suggestions)

    _, exit_code = run_pipeline._apply_description_review_gate(
        [lint_record("a"), lint_record("b")], "unused", tmp_path, None, False, "test-model"
    )

    assert exit_code == 3
    review = {
        e["name"]: e
        for e in json.loads((tmp_path / "vlmd_description_review.json").read_text("utf-8"))
    }
    assert "triage_failed" not in review["a"]
    assert review["b"]["triage_failed"] is True
    assert review["b"]["decision"] == ""

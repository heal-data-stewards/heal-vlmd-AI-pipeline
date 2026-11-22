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


def test_decisions_file_is_applied(monkeypatch, tmp_path):
    def no_triage(*a, **k):
        raise AssertionError("every field has a decision, so triage shouldn't run")

    monkeypatch.setattr(vlmd_description_triage, "triage", no_triage)
    decisions = tmp_path / "decisions.json"
    decisions.write_text(
        json.dumps(
            [
                {"name": "a", "decision": "send_to_llm", "justification": "truncated"},
                {"name": "b", "decision": "leave_as_is"},
            ]
        ),
        encoding="utf-8",
    )
    lint_path = tmp_path / "lint.json"

    records, exit_code = run_pipeline._apply_description_review_gate(
        [lint_record("a"), lint_record("b")], str(lint_path), tmp_path, str(decisions), False, "m"
    )

    assert exit_code is None
    assert [r["name"] for r in records] == ["a"]
    review = json.loads((tmp_path / "vlmd_description_review.json").read_text("utf-8"))
    assert [(e["name"], e["decision"]) for e in review] == [
        ("a", "send_to_llm"),
        ("b", "leave_as_is"),
    ]


def test_decision_a_human_cannot_choose_leaves_the_field_undecided(monkeypatch, tmp_path):
    # flag_for_human and typos aren't decisions; with --yes the field defaults to leave_as_is.
    decisions = tmp_path / "decisions.json"
    decisions.write_text(
        json.dumps(
            [
                {"name": "a", "decision": "flag_for_human"},
                {"name": "b", "decision": "leave as is"},
            ]
        ),
        encoding="utf-8",
    )

    _, exit_code = run_pipeline._apply_description_review_gate(
        [lint_record("a"), lint_record("b")],
        str(tmp_path / "lint.json"),
        tmp_path,
        str(decisions),
        True,
        "m",
    )

    assert exit_code is None
    review = json.loads((tmp_path / "vlmd_description_review.json").read_text("utf-8"))
    assert {e["decision"] for e in review} == {"leave_as_is"}
    assert {e["justification"] for e in review} == {"--yes: not reviewed"}

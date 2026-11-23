"""Unit tests for vlmd_fixup: LLM repair of lint-flagged fields.

Put tests here for fixup() and its helpers. The LLM is never called:
vlmd_fixup.process_chunk is monkeypatched, so these tests cover what fixup does
around each chunk (checkpointing, output files), not the prompts.
"""

import json

import vlmd_fixup


def write_lint(tmp_path, *names):
    lint = tmp_path / "lint.json"
    records = [
        {"name": n, "issues": ["missing_type"], "vlmd_field_draft": {"name": n}} for n in names
    ]
    lint.write_text(json.dumps(records), encoding="utf-8")
    return lint


def fixed(name):
    return {"field": {"name": name, "type": "string"}, "justification": "", "sources": []}


def run_fixup(tmp_path, lint, checkpoint):
    output = tmp_path / "fixes.json"
    exit_code = vlmd_fixup.fixup(str(lint), str(output), str(checkpoint), None, "test-model")
    return exit_code, json.loads(output.read_text(encoding="utf-8"))


# --- Checkpoints ---
# Each chunk is saved as it finishes, tied to a fingerprint of the flagged fields,
# so a re-run on the same input resumes and a changed input starts fresh.


def test_checkpoint_from_an_earlier_run_is_reused(monkeypatch, tmp_path):
    lint = write_lint(tmp_path, "a")
    checkpoint = tmp_path / "checkpoint.json"
    monkeypatch.setattr(
        vlmd_fixup, "process_chunk", lambda *a, **k: [fixed(c["name"]) for c in a[2]]
    )
    assert run_fixup(tmp_path, lint, checkpoint)[0] == 0
    assert checkpoint.exists()

    def no_llm(*a, **k):
        raise AssertionError("the checkpoint covers every chunk")

    monkeypatch.setattr(vlmd_fixup, "process_chunk", no_llm)
    assert run_fixup(tmp_path, lint, checkpoint) == (0, [fixed("a")["field"]])


def test_checkpoint_for_different_fields_is_ignored(monkeypatch, tmp_path):
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(
        json.dumps({"_fingerprint": "zzz:missing_type", "0": [fixed("zzz")]}), encoding="utf-8"
    )
    chunks = []
    monkeypatch.setattr(
        vlmd_fixup,
        "process_chunk",
        lambda *a, **k: chunks.append(a[2]) or [fixed(c["name"]) for c in a[2]],
    )

    assert run_fixup(tmp_path, write_lint(tmp_path, "a"), checkpoint) == (0, [fixed("a")["field"]])
    assert len(chunks) == 1
    assert json.loads(checkpoint.read_text(encoding="utf-8"))["_fingerprint"] == "a:missing_type"

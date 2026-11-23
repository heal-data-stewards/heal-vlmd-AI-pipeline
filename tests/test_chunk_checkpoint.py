"""Unit tests for chunk_checkpoint: saving and loading per-chunk LLM results.

Put tests here for load_checkpoint() and save_checkpoint() on their own. How a
stage uses a checkpoint (what it fingerprints, which chunks it reuses) is tested
in that stage's test file.
"""

import pytest

from chunk_checkpoint import load_checkpoint, save_checkpoint


def test_saved_chunks_load_back(tmp_path):
    path = str(tmp_path / "checkpoint.json")
    chunks = {"0": [{"name": "a"}], "1": [{"name": "é"}]}
    save_checkpoint(path, "fp", chunks)
    assert load_checkpoint(path, "fp") == (chunks, False)


def test_checkpoint_for_different_input_is_stale(tmp_path):
    path = str(tmp_path / "checkpoint.json")
    save_checkpoint(path, "old", {"0": [{"name": "a"}]})
    assert load_checkpoint(path, "new") == ({}, True)


@pytest.mark.parametrize("text", ["not json", "[]", '"a string"'])
def test_unreadable_checkpoint_is_ignored(tmp_path, text):
    path = tmp_path / "checkpoint.json"
    path.write_text(text, encoding="utf-8")
    assert load_checkpoint(str(path), "fp") == ({}, False)


def test_missing_checkpoint_loads_nothing(tmp_path):
    assert load_checkpoint(str(tmp_path / "missing.json"), "fp") == ({}, False)


def test_no_path_saves_and_loads_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_checkpoint(None, "fp", {"0": []})
    assert list(tmp_path.iterdir()) == []
    assert load_checkpoint(None, "fp") == ({}, False)

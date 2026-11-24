"""Checkpoints for the LLM stages that work in chunks (vlmd_fixup, vlmd_description_triage).

A stage saves its results after every chunk, so a re-run after a crash or an
interrupted run picks up where it stopped instead of paying for the same LLM calls
again. The file is a JSON object with one key per chunk index ("0", "1", ...)
holding that chunk's results, plus "_fingerprint": a string the stage builds from
its input. A checkpoint is used only when the fingerprint matches, so changed input
starts fresh. Keys starting with "_" are metadata, not chunks.

Each stage decides what goes into its fingerprint, and which saved chunks it trusts.
"""

import json
from pathlib import Path


def load_checkpoint(path: str | None, fingerprint: str) -> tuple[dict[str, list], bool]:
    """Return (saved chunks keyed by chunk index, stale).

    stale is True when a checkpoint exists but was written for different input; its
    chunks are not returned. A missing path, file or unreadable file gives ({}, False).
    """
    if not path or not Path(path).exists():
        return {}, False
    try:
        saved = json.loads(Path(path).read_text(encoding="utf-8"))
        if saved.get("_fingerprint") != fingerprint:
            return {}, True
        return {k: v for k, v in saved.items() if not k.startswith("_")}, False
    except (OSError, ValueError, AttributeError):  # AttributeError: not a JSON object
        return {}, False


def save_checkpoint(path: str | None, fingerprint: str, chunks: dict[str, list]) -> None:
    """Write every chunk saved so far. A None path saves nothing."""
    if not path:
        return
    Path(path).write_text(
        json.dumps({"_fingerprint": fingerprint, **chunks}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

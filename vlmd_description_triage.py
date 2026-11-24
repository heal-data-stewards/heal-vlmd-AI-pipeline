"""LLM triage for short/placeholder descriptions flagged by the converter.

A word-count/placeholder-string heuristic can't tell a truncated fragment
("not") apart from a legitimate short label ("Header") or a correctly-translated
foreign-language word ("todo" = "all" in Spanish). This module sends flagged
candidates to an LLM with source-row context (table_label, domain, sub_domain,
...) and asks it to classify each as needing a rewrite or acceptable as-is,
with a one-sentence justification. The result pre-fills the human review file —
it does not decide anything on its own; a human still approves before it's used.
"""

import json
import time
from pathlib import Path

from cli_ui import print_progress
from llm_client import call_llm, parse_json_response

MAX_ROWS_PER_CHUNK = 40
MAX_TOKENS_OUT = 4096
PROMPTS_DIR = Path(__file__).parent / "prompts"

SYSTEM_PROMPT = (
    "You are a careful data-dictionary reviewer. Your job is to classify flagged "
    "fields as needing a rewrite or acceptable as-is, using the context given. "
    "You do not rewrite descriptions yourself."
)

CONTEXT_COLUMNS = [
    "description",
    "instruction",
    "table_label",
    "domain",
    "sub_domain",
    "source",
    "type_level",
    "type_data",
    "Field Label",
    "Form Name",
    "Field Type",
    "Section Header",
]


def _context_for(rec: dict) -> dict:
    source_row = rec.get("source_row") or {}
    return {k: v for k, v in source_row.items() if k in CONTEXT_COLUMNS and v}


def triage(
    candidates: list[dict], model_key: str, checkpoint_path: str | None = None
) -> dict[str, dict]:
    """Classify each candidate lint record. Returns {name: {decision, justification}}."""
    if not candidates:
        return {}

    base_prompt = (PROMPTS_DIR / "description_triage_prompt.md").read_text(encoding="utf-8").strip()

    items = [
        {
            "name": rec["name"],
            "description": rec["vlmd_field_draft"].get("description", ""),
            "context": _context_for(rec),
        }
        for rec in candidates
    ]

    fingerprint = "|".join(f"{i['name']}:{i['description']}" for i in items)
    checkpoint: dict = {}
    if checkpoint_path and Path(checkpoint_path).exists():
        try:
            saved = json.loads(Path(checkpoint_path).read_text(encoding="utf-8"))
            if saved.get("_fingerprint") == fingerprint:
                checkpoint = {k: v for k, v in saved.items() if not k.startswith("_")}
        except (OSError, ValueError, AttributeError):
            checkpoint = {}

    chunks = [items[i : i + MAX_ROWS_PER_CHUNK] for i in range(0, len(items), MAX_ROWS_PER_CHUNK)]
    results: dict[str, dict] = {}

    t_start = time.time()
    for i, chunk in enumerate(chunks):
        chunk_key = str(i)
        if chunk_key in checkpoint:
            for entry in checkpoint[chunk_key]:
                results[entry["name"]] = entry
            print_progress(i + 1, len(chunks), time.time() - t_start, label="Triage")
            continue

        user_msg = base_prompt + "\n\n" + json.dumps(chunk, indent=2, ensure_ascii=False)
        raw, ok, err = call_llm(
            SYSTEM_PROMPT, user_msg, model_key=model_key, max_tokens=MAX_TOKENS_OUT
        )
        if not ok:
            print(flush=True)
            raise RuntimeError(f"Triage LLM call failed: {err}")

        parsed = parse_json_response(raw)
        if not isinstance(parsed, list):
            print(flush=True)
            # Malformed LLM output is a bad value, not a caller passing the wrong type.
            raise ValueError(f"Expected JSON array from triage, got {type(parsed).__name__}")  # noqa: TRY004

        for entry in parsed:
            if entry.get("decision") not in ("send_to_llm", "leave_as_is", "flag_for_human"):
                entry["decision"] = (
                    "flag_for_human"  # safe fallback: don't guess, don't auto-approve
                )
            results[entry["name"]] = entry

        checkpoint[chunk_key] = parsed
        if checkpoint_path:
            Path(checkpoint_path).write_text(
                json.dumps(
                    {"_fingerprint": fingerprint, **checkpoint}, indent=2, ensure_ascii=False
                ),
                encoding="utf-8",
            )

        print_progress(i + 1, len(chunks), time.time() - t_start, label="Triage")

    print(flush=True)
    return results

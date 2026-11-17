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
VALID_DECISIONS = ("send_to_llm", "leave_as_is", "flag_for_human")
RETRY_REMINDER = (
    "\n\nIMPORTANT: The previous response could not be used. Return ONLY a valid JSON "
    "array with exactly one object per field above, each with the field's exact "
    '"name", a "decision" and a "justification". No prose or code fences.'
)

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


def _named_entries(parsed: object, names: set[str]) -> dict[str, dict]:
    """The usable entries in a parsed triage reply (or checkpointed chunk), keyed by name.

    Only objects whose "name" is a string naming a field in the chunk are kept, so
    made-up or altered names, and unhashable ones, are dropped. An unknown decision
    becomes flag_for_human: don't guess, don't auto-approve.
    """
    if not isinstance(parsed, list):
        return {}
    entries: dict[str, dict] = {}
    for e in parsed:
        if isinstance(e, dict) and isinstance(e.get("name"), str) and e["name"] in names:
            if e.get("decision") not in VALID_DECISIONS:
                e["decision"] = "flag_for_human"
            entries[e["name"]] = e
    return entries


def _ask(user_msg: str, names: set[str], model_key: str) -> dict[str, dict]:
    raw, ok, err = call_llm(SYSTEM_PROMPT, user_msg, model_key=model_key, max_tokens=MAX_TOKENS_OUT)
    if not ok:
        print(flush=True)
        raise RuntimeError(f"Triage LLM call failed: {err}")
    try:
        parsed = parse_json_response(raw)
    except ValueError:  # json.JSONDecodeError
        return {}
    return _named_entries(parsed, names)


def _triage_chunk(
    chunk: list[dict], base_prompt: str, model_key: str, chunk_number: int
) -> tuple[list[dict], bool]:
    """Triage one chunk. Returns (one entry per field in chunk order, complete).

    A reply that doesn't cover every field is retried once with a reminder of the
    expected shape. Fields still uncovered after that are flagged for a human with
    "triage_failed": true rather than aborting the run, since a human reviews every
    suggestion anyway; complete is False then, so the caller doesn't checkpoint the
    chunk and a re-run asks the LLM again.
    """
    names = [item["name"] for item in chunk]
    wanted = set(names)
    user_msg = base_prompt + "\n\n" + json.dumps(chunk, indent=2, ensure_ascii=False)

    found = _ask(user_msg, wanted, model_key)
    if len(found) < len(wanted):
        # Entries from the first reply are kept; the retry fills gaps and wins on overlap.
        found.update(_ask(user_msg + RETRY_REMINDER, wanted, model_key))

    missing = [n for n in names if n not in found]
    if missing:
        print(
            f"\n  WARNING: triage gave no usable suggestion for {len(missing)} of "
            f"{len(names)} field(s) in chunk {chunk_number}, even after a retry; "
            "flagging them for human review.",
            flush=True,
        )
        for n in missing:
            found[n] = {
                "name": n,
                "decision": "flag_for_human",
                "justification": "LLM triage gave no usable suggestion; review by hand.",
                "triage_failed": True,
            }
    return [found[n] for n in names], not missing


def triage(
    candidates: list[dict], model_key: str, checkpoint_path: str | None = None
) -> dict[str, dict]:
    """Classify each candidate lint record. Returns {name: {decision, justification}}.

    Every candidate gets an entry. One the LLM gave no usable suggestion for is
    flag_for_human with "triage_failed": True.
    """
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
        names = [item["name"] for item in chunk]
        # A checkpointed chunk is reused only if it covers every field, so a partial
        # one written by an older version of this module is asked about again.
        saved_entries = _named_entries(checkpoint.get(chunk_key), set(names))
        if len(saved_entries) == len(names):
            entries = [saved_entries[n] for n in names]
        else:
            entries, complete = _triage_chunk(chunk, base_prompt, model_key, i + 1)
            if complete:
                checkpoint[chunk_key] = entries
                if checkpoint_path:
                    Path(checkpoint_path).write_text(
                        json.dumps(
                            {"_fingerprint": fingerprint, **checkpoint},
                            indent=2,
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )

        for entry in entries:
            results[entry["name"]] = entry
        print_progress(i + 1, len(chunks), time.time() - t_start, label="Triage")

    print(flush=True)
    return results

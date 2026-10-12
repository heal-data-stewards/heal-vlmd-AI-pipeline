"""Send lint-flagged fields to LLM for fixup.

Reads prompt paths from the format YAML so each format can have its own
context-specific fixup instructions. Falls back to generic_fixup_prompt.md.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import tiktoken
import yaml

from cli_ui import print_progress
from llm_client import DEFAULT_MODEL, MODELS, call_llm, parse_json_response

MAX_ROWS_PER_CHUNK = (
    10  # conservative: wrapper format (field+justification+sources) is ~3x bare fields
)
MAX_TOKENS_OUT = 8192
MAX_RETRIES = 2
ENCODING = tiktoken.get_encoding("cl100k_base")
PROMPTS_DIR = Path(__file__).parent / "prompts"

# Rough token budget: warn if input alone suggests the output may overflow
# (each record ≈ 200 tokens of metadata on top of the field content)
OUTPUT_OVERHEAD_PER_RECORD = 200


def count_tokens(text: str) -> int:
    return len(ENCODING.encode(text))


def load_prompts_from_spec(format_yaml: str | None) -> tuple[str, str]:
    """Load system + user prompts, preferring paths from format YAML."""
    defaults = {
        "system": PROMPTS_DIR / "system_invariants_vlmd.md",
        "user": PROMPTS_DIR / "generic_fixup_prompt.md",
    }

    if format_yaml and Path(format_yaml).exists():
        spec = yaml.safe_load(Path(format_yaml).read_text(encoding="utf-8"))
        base = Path(format_yaml).parent.parent  # heal-vlmd-pipeline/
        fp = spec.get("fixup_prompt")
        sp = spec.get("system_invariants_prompt")
        if fp:
            defaults["user"] = base / fp
        if sp:
            defaults["system"] = base / sp

    return (
        defaults["system"].read_text(encoding="utf-8").strip(),
        defaults["user"].read_text(encoding="utf-8").strip(),
    )


def process_chunk(
    system: str, base_prompt: str, chunk: list, model_key: str, attempt: int = 0
) -> list:
    user_msg = (
        base_prompt + "\n\nFields to fix:\n" + json.dumps(chunk, indent=2, ensure_ascii=False)
    )

    # Warn if input is large enough that output might still overflow
    input_tokens = count_tokens(system + user_msg)
    est_output = len(chunk) * OUTPUT_OVERHEAD_PER_RECORD + input_tokens // 2
    if est_output > MAX_TOKENS_OUT * 0.85:
        print(
            f"  WARNING: chunk may be near token limit "
            f"(~{est_output} est. output tokens, limit {MAX_TOKENS_OUT}). "
            "Consider reducing MAX_ROWS_PER_CHUNK.",
            flush=True,
        )

    raw, ok, err = call_llm(system, user_msg, model_key=model_key, max_tokens=MAX_TOKENS_OUT)

    if not ok:
        raise RuntimeError(f"LLM call failed: {err}")

    try:
        parsed = parse_json_response(raw)
    except (json.JSONDecodeError, ValueError) as e:
        if attempt < MAX_RETRIES:
            print(f"  JSON parse error (attempt {attempt + 1}): {e} — retrying", flush=True)
            retry_msg = (
                user_msg + "\n\nIMPORTANT: Previous response could not be parsed. "
                "Return ONLY a valid JSON array, no prose or code fences."
            )
            raw2, ok2, err2 = call_llm(system, retry_msg, model_key=model_key, max_tokens=4096)
            if not ok2:
                raise RuntimeError(f"LLM retry failed: {err2}")
            parsed = parse_json_response(raw2)
        else:
            raise RuntimeError(f"JSON parse failed after {MAX_RETRIES} retries: {e}")

    if not isinstance(parsed, list):
        # Malformed LLM output is a bad value, not a caller passing the wrong type.
        raise ValueError(f"Expected JSON array, got {type(parsed).__name__}")  # noqa: TRY004

    # Unwrap {field, justification, sources} wrapper if present
    unwrapped = []
    for item in parsed:
        if isinstance(item, dict) and "field" in item:
            unwrapped.append(item)
        else:
            # LLM returned bare field (fallback for older prompt versions)
            unwrapped.append({"field": item, "justification": "", "sources": []})
    return unwrapped


def allowed_context_columns(format_yaml: str | None) -> set[str] | None:
    """Source columns worth sending to the LLM as context, per the format spec.

    Was previously a hardcoded REDCap/HBCD-specific allowlist ("Field Label",
    "Form Name", ...) that silently matched nothing for any other format —
    e.g. cde.yaml's fields all got an empty source_row, so fixup ran on
    almost no context. Derive the allowlist from the format spec itself
    instead: everything it explicitly maps, plus custom_columns.

    Returns None (no restriction — send everything) if the spec can't be
    loaded, so an unresolvable format_yaml fails open rather than silently
    stripping all context again.
    """
    if not format_yaml or not Path(format_yaml).exists():
        return None
    spec = yaml.safe_load(Path(format_yaml).read_text(encoding="utf-8"))
    cols: set[str] = set(spec.get("custom_columns") or [])
    for key in ("name_column", "description_column", "title_column"):
        v = spec.get(key)
        if isinstance(v, str):
            cols.add(v)
        elif isinstance(v, dict) and "combine" in v:
            cols.update(v["combine"])
        elif isinstance(v, list):
            cols.update(c for c in v if c)
    for section, key in (
        ("type_mapping", "source_column"),
        ("levels", "source_column"),
        ("section", "primary_column"),
    ):
        v = (spec.get(section) or {}).get(key)
        if v:
            cols.add(v)
    return cols


def fixup(
    lint_path: str,
    output_path: str,
    checkpoint_path: str,
    format_yaml: str | None,
    model_key: str,
    cleanup_log_path: str | None = None,
) -> int:
    lint_records = json.loads(Path(lint_path).read_text(encoding="utf-8"))
    if not lint_records:
        print("No lint records to fix.", flush=True)
        Path(output_path).write_text("[]", encoding="utf-8")
        return 0

    allowed_cols = allowed_context_columns(format_yaml)
    items_for_llm = [
        {
            "name": rec["name"],
            "issues": rec["issues"],
            "source_row": {
                k: v
                for k, v in (rec.get("source_row") or rec.get("hbcd_row", {})).items()
                if allowed_cols is None or k in allowed_cols
            },
            "vlmd_field_draft": rec["vlmd_field_draft"],
        }
        for rec in lint_records
    ]

    print(f"Fixing {len(items_for_llm)} flagged fields with model {model_key!r} ...", flush=True)
    for item in items_for_llm:
        print(f"  [{item['name']}]  will fix: {', '.join(item['issues'])}", flush=True)
    system, base_prompt = load_prompts_from_spec(format_yaml)

    # Fingerprint the current input so stale checkpoints are detected
    input_fingerprint = "|".join(
        f"{item['name']}:{','.join(item['issues'])}" for item in items_for_llm
    )

    # Load checkpoint — invalidate if the flagged field list changed
    checkpoint = {}
    if Path(checkpoint_path).exists():
        try:
            saved = json.loads(Path(checkpoint_path).read_text(encoding="utf-8"))
            if saved.get("_fingerprint") == input_fingerprint:
                checkpoint = {k: v for k, v in saved.items() if not k.startswith("_")}
                print(f"  Resuming from checkpoint ({len(checkpoint)} chunks done)", flush=True)
            else:
                print(
                    "  Checkpoint invalidated — flagged fields changed since last run. "
                    "Starting fresh.",
                    flush=True,
                )
        except (OSError, ValueError, AttributeError):
            checkpoint = {}

    chunks = [
        items_for_llm[i : i + MAX_ROWS_PER_CHUNK]
        for i in range(0, len(items_for_llm), MAX_ROWS_PER_CHUNK)
    ]

    # all_wrapped: list of {field, justification, sources} dicts
    all_wrapped = []
    t_start = time.time()
    for i, chunk in enumerate(chunks):
        chunk_key = str(i)
        if chunk_key in checkpoint:
            all_wrapped.extend(checkpoint[chunk_key])
            print_progress(i + 1, len(chunks), time.time() - t_start, label="Fixup")
            continue

        try:
            wrapped = process_chunk(system, base_prompt, chunk, model_key)
        # Any chunk failure stops the stage with exit 1; the checkpoint lets a re-run resume.
        except Exception as e:  # noqa: BLE001
            print(f"\nERROR in chunk {i}: {e}", file=sys.stderr)
            return 1

        checkpoint[chunk_key] = wrapped
        Path(checkpoint_path).write_text(
            json.dumps(
                {"_fingerprint": input_fingerprint, **checkpoint}, indent=2, ensure_ascii=False
            ),
            encoding="utf-8",
        )
        all_wrapped.extend(wrapped)
        print_progress(i + 1, len(chunks), time.time() - t_start, label="Fixup")
    print(flush=True)

    # Print a capped preview of LLM decisions — the full reasoning for every
    # field lives in the cleanup log (see cleanup_log_path below), so this is
    # just enough to spot-check, not a dump of everything.
    preview_limit = 10
    for item in all_wrapped[:preview_limit]:
        name = item["field"].get("name", "?")
        justification = item.get("justification", "")
        print(f"  [{name}]", flush=True)
        if justification:
            print(f"    Justification: {justification}", flush=True)
    if len(all_wrapped) > preview_limit:
        print(
            f"  ... and {len(all_wrapped) - preview_limit} more "
            f"(full reasoning in the cleanup log)",
            flush=True,
        )

    # Extract bare VLMD fields for the merge step
    all_fixed = [item["field"] for item in all_wrapped]
    Path(output_path).write_text(
        json.dumps(all_fixed, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  Fixed fields → {output_path}", flush=True)

    # Write cleanup log: issues, before/after diff, LLM justification and sources
    if cleanup_log_path:
        by_name = {r["name"]: r for r in lint_records}
        cleanup_log = []
        for item in all_wrapped:
            fixed_field = item["field"]
            name = fixed_field.get("name")
            original = by_name.get(name, {})
            before = original.get("vlmd_field_draft", {})
            cleanup_log.append(
                {
                    "name": name,
                    "model": model_key,
                    "issues": original.get("issues", []),
                    "justification": item.get("justification", ""),
                    "sources": item.get("sources", []),
                    "before": before,
                    "after": fixed_field,
                    "changed_keys": [k for k in fixed_field if fixed_field.get(k) != before.get(k)],
                }
            )
        Path(cleanup_log_path).write_text(
            json.dumps(cleanup_log, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"  LLM cleanup log → {cleanup_log_path}", flush=True)

    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lint", default="work/vlmd_lint_report.json")
    ap.add_argument("--output", default="work/vlmd_llm_fixes.json")
    ap.add_argument("--checkpoint", default="work/vlmd_llm_fixes.checkpoint.json")
    ap.add_argument("--format", default=None, help="Format YAML path (for prompt selection)")
    ap.add_argument("--model", default=DEFAULT_MODEL, choices=list(MODELS))
    args = ap.parse_args()
    sys.exit(fixup(args.lint, args.output, args.checkpoint, args.format, args.model))


if __name__ == "__main__":
    main()

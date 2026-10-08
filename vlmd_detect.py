"""Format detection engine for the VLMD conversion pipeline.

Pass 1 — Rule-based scoring using detection.signature_columns from format YAMLs.
          Also checks file_extensions for definitive matches (e.g. .dta → stata).
Pass 2 — LLM fallback for unknown or ambiguous formats.
          Returns a proposed column mapping the user can confirm/correct.

Output is a structured dict that both the CLI and the Claude Code skill consume.
"""
import argparse
import json
import sys
from pathlib import Path

import yaml

from llm_client import DEFAULT_MODEL, call_llm, parse_json_response

FORMATS_DIR = Path(__file__).parent / "formats"
PROMPTS_DIR = Path(__file__).parent / "prompts"
SAMPLE_ROWS = 5
AMBIGUITY_GAP = 0.20   # top score must beat 2nd by this much to be non-ambiguous
HIGH_CONFIDENCE = 0.65


def _detect_encoding(file_path: str) -> str:
    """Try UTF-8 first, fall back to latin-1 (covers most Windows-encoded CSVs)."""
    try:
        with open(file_path, encoding="utf-8") as f:
            f.read(4096)
        return "utf-8"
    except UnicodeDecodeError:
        return "latin-1"


# ── helpers ──────────────────────────────────────────────────────────────────

def load_all_formats(formats_dir: Path = FORMATS_DIR) -> dict[str, dict]:
    """Load all YAML files from formats/ directory. Keys are format_name values."""
    specs = {}
    for f in sorted(formats_dir.glob("*.yaml")):
        try:
            spec = yaml.safe_load(f.read_text(encoding="utf-8"))
            key = spec.get("format_name", f.stem)
            spec["_yaml_path"] = str(f)
            specs[key] = spec
        except Exception as e:
            print(f"  WARNING: could not load {f.name}: {e}", file=sys.stderr)
    return specs


def read_file_preview(file_path: str) -> tuple[list[str], list[dict], int]:
    """
    Returns (columns, sample_rows, total_rows).
    Handles CSV and Stata.
    """
    p = Path(file_path)
    if p.suffix.lower() == ".dta":
        try:
            import pyreadstat
            df, meta = pyreadstat.read_dta(str(p))
            cols = list(df.columns)
            rows = df.head(SAMPLE_ROWS).to_dict(orient="records")
            return cols, rows, len(df)
        except ImportError:
            raise ImportError("pyreadstat required for Stata files: pip install pyreadstat")
    else:
        import pandas as pd
        encoding = _detect_encoding(file_path)
        df = pd.read_csv(file_path, dtype=str, keep_default_na=False,
                         low_memory=False, nrows=SAMPLE_ROWS + 1, encoding=encoding)
        cols = list(df.columns)
        rows = df.head(SAMPLE_ROWS).to_dict(orient="records")
        total = sum(1 for _ in open(file_path, encoding=encoding, errors="replace")) - 1
        return cols, rows, total


def score_format(columns: set[str], spec: dict) -> float:
    detection = spec.get("detection", {})
    strong = detection.get("signature_columns", {}).get("strong", [])
    supporting = detection.get("signature_columns", {}).get("supporting", [])

    if not strong and not supporting:
        return 0.0

    strong_matched = sum(1 for c in strong if c in columns)
    supporting_matched = sum(1 for c in supporting if c in columns)
    total_weight = 2 * len(strong) + len(supporting)
    if total_weight == 0:
        return 0.0
    return (2 * strong_matched + supporting_matched) / total_weight


def check_extension_match(file_path: str, specs: dict) -> tuple[str, dict] | tuple[None, None]:
    """Return (format_name, spec) if file extension is a definitive match."""
    suffix = Path(file_path).suffix.lower()
    for name, spec in specs.items():
        exts = spec.get("detection", {}).get("file_extensions", [])
        if suffix in exts:
            return name, spec
    return None, None


# ── LLM fallback ─────────────────────────────────────────────────────────────

def llm_detect(columns: list[str], sample_rows: list[dict],
               model_key: str = DEFAULT_MODEL) -> dict:
    system = (PROMPTS_DIR / "system_invariants_vlmd.md").read_text(encoding="utf-8")
    prompt_template = (PROMPTS_DIR / "detect_format_prompt.md").read_text(encoding="utf-8")

    sample_display = json.dumps(sample_rows[:3], indent=2, ensure_ascii=False)
    user_msg = (
        f"{prompt_template}\n\n"
        f"## File columns ({len(columns)} total)\n\n"
        + "\n".join(f"  - {c}" for c in columns)
        + f"\n\n## Sample rows (first 3)\n\n```json\n{sample_display}\n```"
    )

    raw, ok, err = call_llm(system, user_msg, model_key=model_key, max_tokens=2048)
    if not ok:
        raise RuntimeError(f"LLM detection failed: {err}")

    try:
        return parse_json_response(raw)
    except (json.JSONDecodeError, ValueError) as e:
        raise RuntimeError(f"LLM returned invalid JSON: {e}\n\nRaw output:\n{raw[:500]}")


# ── main detection function ───────────────────────────────────────────────────

def detect(file_path: str,
           formats_dir: str | None = None,
           model_key: str = DEFAULT_MODEL,
           llm_fallback: bool = True) -> dict:
    """
    Returns a result dict:
    {
        format_name:       str | None,
        confidence:        float,
        method:            "extension" | "rule_based" | "llm" | "unknown",
        reasoning:         str,
        format_yaml_path:  str | None,
        row_count:         int,
        column_count:      int,
        columns:           list[str],
        sample_rows:       list[dict],
        scores:            dict[str, float],
        proposed_mapping:  dict | None,   # set for unknown/llm-inferred formats
        ambiguous:         bool,
    }
    """
    fdir = Path(formats_dir) if formats_dir else FORMATS_DIR
    specs = load_all_formats(fdir)

    print(f"Reading {file_path} ...", flush=True)
    columns, sample_rows, row_count = read_file_preview(file_path)
    col_set = set(columns)
    print(f"  {row_count:,} rows, {len(columns)} columns", flush=True)

    # Pass 0 — extension check (definitive)
    ext_name, ext_spec = check_extension_match(file_path, specs)
    if ext_name:
        print(f"  Extension match: {ext_name}", flush=True)
        return {
            "format_name": ext_name,
            "confidence": 1.0,
            "method": "extension",
            "reasoning": f"File extension {Path(file_path).suffix!r} matches {ext_name} format.",
            "format_yaml_path": ext_spec["_yaml_path"],
            "row_count": row_count,
            "column_count": len(columns),
            "columns": columns,
            "sample_rows": sample_rows,
            "scores": {ext_name: 1.0},
            "proposed_mapping": None,
            "ambiguous": False,
        }

    # Pass 1 — rule-based scoring
    scores = {}
    for name, spec in specs.items():
        threshold = spec.get("detection", {}).get("confidence_threshold", 0.7)
        if threshold > 1.0:
            continue  # generic-csv: skip scoring, it's the explicit fallback
        scores[name] = score_format(col_set, spec)

    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    print("  Scores:", {k: f"{v:.2f}" for k, v in sorted_scores[:4]}, flush=True)

    if sorted_scores:
        top_name, top_score = sorted_scores[0]
        second_score = sorted_scores[1][1] if len(sorted_scores) > 1 else 0.0
        threshold = specs[top_name].get("detection", {}).get("confidence_threshold", 0.6)

        if top_score >= threshold and (top_score - second_score) >= AMBIGUITY_GAP:
            return {
                "format_name": top_name,
                "confidence": round(top_score, 3),
                "method": "rule_based",
                "reasoning": _build_rule_reasoning(top_name, specs[top_name], col_set, top_score),
                "format_yaml_path": specs[top_name]["_yaml_path"],
                "row_count": row_count,
                "column_count": len(columns),
                "columns": columns,
                "sample_rows": sample_rows,
                "scores": dict(sorted_scores),
                "proposed_mapping": None,
                "ambiguous": False,
            }

        if top_score >= threshold:
            # High score but close race — flag as ambiguous, still propose top
            return {
                "format_name": top_name,
                "confidence": round(top_score, 3),
                "method": "rule_based",
                "reasoning": _build_rule_reasoning(top_name, specs[top_name], col_set, top_score),
                "format_yaml_path": specs[top_name]["_yaml_path"],
                "row_count": row_count,
                "column_count": len(columns),
                "columns": columns,
                "sample_rows": sample_rows,
                "scores": dict(sorted_scores),
                "proposed_mapping": None,
                "ambiguous": True,
            }

    # Pass 2 — LLM fallback
    if not llm_fallback:
        return {
            "format_name": None,
            "confidence": 0.0,
            "method": "unknown",
            "reasoning": "No format matched and LLM fallback disabled.",
            "format_yaml_path": None,
            "row_count": row_count,
            "column_count": len(columns),
            "columns": columns,
            "sample_rows": sample_rows,
            "scores": dict(sorted_scores) if sorted_scores else {},
            "proposed_mapping": None,
            "ambiguous": False,
        }

    print("  No strong rule-based match — calling LLM for inference ...", flush=True)
    llm_result = llm_detect(columns, sample_rows, model_key=model_key)

    # Models occasionally wrap the requested object in a single-element array —
    # unwrap rather than discard a perfectly usable mapping proposal.
    if isinstance(llm_result, list) and len(llm_result) == 1 and isinstance(llm_result[0], dict):
        llm_result = llm_result[0]

    if not isinstance(llm_result, dict):
        print(f"  WARNING: LLM returned unexpected type {type(llm_result).__name__} — treating as no match", flush=True)
        llm_result = {}

    proposed = llm_result.get("proposed_mapping", {})
    guessed_format = llm_result.get("format_guess")

    # If LLM recognized a known format, point at its YAML
    yaml_path = None
    if guessed_format and guessed_format in specs:
        yaml_path = specs[guessed_format]["_yaml_path"]

    return {
        "format_name": guessed_format,
        "confidence": llm_result.get("confidence", 0.0),
        "method": "llm",
        "reasoning": llm_result.get("reasoning", ""),
        "format_yaml_path": yaml_path,
        "row_count": row_count,
        "column_count": len(columns),
        "columns": columns,
        "sample_rows": sample_rows,
        "scores": dict(sorted_scores) if sorted_scores else {},
        "proposed_mapping": proposed,
        "column_explanations": llm_result.get("column_explanations", {}),
        "ambiguous": False,
    }


def _build_rule_reasoning(name: str, spec: dict, col_set: set, score: float) -> str:
    det = spec.get("detection", {}).get("signature_columns", {})
    strong = det.get("strong", [])
    supporting = det.get("supporting", [])
    found_strong = [c for c in strong if c in col_set]
    found_supporting = [c for c in supporting if c in col_set]
    parts = []
    if found_strong:
        parts.append(f"Found {len(found_strong)}/{len(strong)} strong signature columns: {found_strong}")
    if found_supporting:
        parts.append(f"Found {len(found_supporting)}/{len(supporting)} supporting columns: {found_supporting}")
    return f"{name.upper()} format (score {score:.0%}). " + ". ".join(parts) + "."


# ── display helpers (used by skill) ──────────────────────────────────────────

def format_detection_message(result: dict, interactive: bool = True) -> str:
    """Human-readable summary of a detection result.

    `interactive=True` (the default) appends a closing question aimed at a
    live conversational consumer (e.g. a chat-driven skill) that can actually
    collect an answer. Non-interactive callers — run_pipeline.py's batch CLI,
    which has no way to receive a reply to a question it prints — should pass
    `interactive=False` to get just the factual summary.
    """
    lines = []
    fmt = result["format_name"]
    conf = result["confidence"]
    method = result["method"]

    if fmt and not result.get("ambiguous") and method != "llm":
        lines.append(
            f"I identified this as **{fmt.upper()} format** "
            f"({conf:.0%} confident, {method})."
        )
        lines.append(f"\n{result['reasoning']}")
        lines.append(f"\n**{result['row_count']:,} rows** · {result['column_count']} columns")
        if interactive:
            lines.append("\nShould I proceed with the VLMD conversion?")

    elif fmt and result.get("ambiguous"):
        lines.append(f"This looks most like **{fmt.upper()} format** but I'm not certain.")
        lines.append(f"\n{result['reasoning']}")
        top_scores = sorted(result.get("scores", {}).items(), key=lambda x: -x[1])[:3]
        lines.append("\nTop candidates:")
        for n, s in top_scores:
            lines.append(f"  - {n}: {s:.0%}")
        if interactive:
            lines.append("\nIs this the right format, or is it something else?")

    elif method == "llm":
        proposed = result.get("proposed_mapping", {})
        explanations = result.get("column_explanations", {})
        if proposed:
            lines.append("I don't recognize this format. Here's what I found:\n")
        else:
            lines.append(
                "I don't recognize this format, and the AI's attempt to propose a "
                "mapping failed (malformed response) — no mapping is available.\n"
            )
        mapping_display = [
            ("name", proposed.get("name_column")),
            ("description", proposed.get("description_column")),
            ("title", proposed.get("title_column")),
            ("type", (proposed.get("type_mapping") or {}).get("source_column")),
            ("levels/choices", (proposed.get("levels") or {}).get("source_column")),
            ("section", (proposed.get("section") or {}).get("primary_column")),
            ("minimum", proposed.get("minimum_column")),
            ("maximum", proposed.get("maximum_column")),
            ("value labels", proposed.get("value_labels_column")),
        ]
        for vlmd_prop, src_col in mapping_display:
            if src_col:
                note = explanations.get(src_col, "")
                lines.append(f"  - **'{src_col}'** → `{vlmd_prop}`" + (f" ({note})" if note else ""))

        custom = proposed.get("custom_columns", [])
        if custom:
            lines.append(f"  - Remaining columns → `custom`: {custom}")

        if interactive:
            lines.append(
                "\nDoes this look right? Tell me what to change, or say **'looks good'** "
                "to save this mapping and start the conversion.\n"
                "(I'll save this as a reusable format for your study.)"
            )

    else:
        lines.append("I couldn't identify the format of this file.")
        lines.append(f"\nColumns found: {result['columns']}")
        if interactive:
            lines.append("\nWhat format is this? Or tell me how to map these columns to VLMD.")

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Path to data dictionary file")
    ap.add_argument("--formats-dir", default=str(FORMATS_DIR))
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--no-llm", action="store_true", help="Skip LLM fallback")
    ap.add_argument("--output-json", default=None, help="Write result JSON to file")
    args = ap.parse_args()

    result = detect(
        args.input,
        formats_dir=args.formats_dir,
        model_key=args.model,
        llm_fallback=not args.no_llm,
    )

    msg = format_detection_message(result)
    print("\n" + msg)

    if args.output_json:
        # Exclude sample_rows from JSON output to keep it manageable
        out = {k: v for k, v in result.items() if k != "sample_rows"}
        Path(args.output_json).write_text(
            json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nDetection result → {args.output_json}", flush=True)

    # Exit codes: 0=known format, 1=unknown (needs interview). Errors raise, which
    # Python also reports as exit 1.
    if result["format_name"] is None:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()

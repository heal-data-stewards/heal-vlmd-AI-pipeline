"""Conversational VLMD conversion pipeline — prototype (issue #15).

Unlike run_pipeline.py (which stays the non-interactive batch entry point),
this is a Python generator that pauses at defined checkpoints and resumes via
.send(answer) — the foundation for the vlmd-app /chat endpoints. It imports
the same step functions run_pipeline.py uses; no pipeline logic is
duplicated here, only reordered with yield points.

State machine:

    ingest -> mapping* -> convert -> confirm_llm_columns* -> fixup -> validate -> merge -> done

(* = pause point. validate-phase loop-back to mapping, per the original epic,
isn't wired up yet.)

Usage (standalone terminal demo):
    python chat_pipeline.py --input demo/demo_dd.csv --hdp-id DEMO001
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PIPELINE_DIR = Path(__file__).parent
WORK_DIR = PIPELINE_DIR / "work"
OUTPUT_DIR = PIPELINE_DIR / "output"


@dataclass
class Prompt:
    kind: str                    # "confirm_mapping" | "cancelled" | "done" | "error"
    message: str
    payload: dict[str, Any] = field(default_factory=dict)


def run_conversational(
    input_file: str,
    hdp_id: str = "",
    appl_id: str = "unknown",
    title: str = "HEAL Study Data Dictionary",
    model: str = "azure-gpt-4.1-mini",
    output_dir: Path = OUTPUT_DIR,
    format_yaml: str | None = None,
):
    """Generator: yields Prompt objects at pause points, resumes via .send(answer)."""
    file_stem = Path(input_file).stem.replace(" ", "_")
    run_key = hdp_id or "scratch"
    work_subdir = WORK_DIR / run_key
    work_subdir.mkdir(parents=True, exist_ok=True)
    vlmd_out = output_dir / run_key / "vlmd" / file_stem
    vlmd_out.mkdir(parents=True, exist_ok=True)

    # ── ingest ──────────────────────────────────────────────────────────────
    suffix = Path(input_file).suffix.lower()
    if suffix == ".pdf":
        from vlmd_pdf import extract as pdf_extract
        extracted = str(work_subdir / f"{file_stem}_extracted.csv")
        pdf_extract(input_file, extracted, model_key=model)
        input_file = extracted
    elif suffix in (".xlsx", ".xls"):
        from vlmd_excel import extract as excel_extract
        extracted = str(work_subdir / f"{file_stem}_extracted.csv")
        excel_extract(input_file, extracted)
        input_file = extracted

    # ── mapping phase — pause for confirmation ─────────────────────────────
    if format_yaml is None:
        from vlmd_detect import detect

        detection = detect(input_file, formats_dir=str(PIPELINE_DIR / "formats"), model_key=model)

        unrecognized = detection["format_name"] is None
        if unrecognized:
            answer = yield Prompt(
                kind="confirm_mapping",
                message=(
                    "I don't recognize this format automatically, but here's my best guess "
                    "at how to map its columns. Look right? (yes / no — saved as a reusable "
                    "format for this study if you confirm)"
                ),
                payload={"proposed_mapping": detection.get("proposed_mapping"), "raw": detection},
            )
        else:
            answer = yield Prompt(
                kind="confirm_mapping",
                message=(
                    f"This looks like **{detection['format_name'].upper()}** format "
                    f"({detection['confidence']:.0%} confident). "
                    f"{detection['row_count']} rows, {detection['column_count']} columns. "
                    "Proceed with conversion? (yes / no)"
                ),
                payload={"format_name": detection["format_name"], "raw": detection},
            )

        if str(answer).strip().lower() not in ("yes", "y", "looks good", "ok", "confirm"):
            yield Prompt(kind="cancelled", message="Okay, stopping here — nothing was converted.")
            return

        if unrecognized:
            proposed = detection.get("proposed_mapping")
            if not proposed:
                yield Prompt(
                    kind="error",
                    message="The AI couldn't propose a usable mapping for this file's columns, "
                            "so I can't convert it yet.",
                )
                return
            from vlmd_interview import save_format

            format_yaml = save_format(
                applid=appl_id if appl_id != "unknown" else file_stem,
                mapping=proposed,
                hdp_id=hdp_id,
                source_file=Path(input_file).name,
                columns=detection.get("columns"),
            )
        else:
            format_yaml = detection["format_yaml_path"]

        if not format_yaml:
            yield Prompt(
                kind="error",
                message="No usable format mapping was available, so I can't convert this file yet.",
            )
            return
    else:
        answer = yield Prompt(
            kind="confirm_mapping",
            message=f"Using explicitly specified format: {format_yaml}. Proceed with conversion? (yes / no)",
            payload={"format_yaml": format_yaml},
        )
        if str(answer).strip().lower() not in ("yes", "y", "looks good", "ok", "confirm"):
            yield Prompt(kind="cancelled", message="Okay, stopping here — nothing was converted.")
            return

    import yaml
    format_name = yaml.safe_load(Path(format_yaml).read_text()).get("format_name", "unknown")

    # ── convert ─────────────────────────────────────────────────────────────
    from vlmd_convert import convert

    converted_path = str(work_subdir / f"{file_stem}_chat_converted.json")
    lint_path = str(work_subdir / f"{file_stem}_chat_lint.json")
    convert(input_file, format_yaml, converted_path, lint_path)
    lint_records = json.loads(Path(lint_path).read_text())

    # ── convert phase pause — confirm what goes to the LLM ─────────────────
    fixes_path = None
    if lint_records:
        from vlmd_fixup import allowed_context_columns, fixup

        allowed_cols = allowed_context_columns(format_yaml)
        cols_seen: set[str] = set()
        for rec in lint_records:
            cols_seen.update((rec.get("source_row") or {}).keys())
        cols_to_send = sorted(cols_seen & allowed_cols) if allowed_cols is not None else sorted(cols_seen)

        answer = yield Prompt(
            kind="confirm_llm_columns",
            message=(
                f"{len(lint_records)} field(s) need LLM help ({', '.join(sorted({i for r in lint_records for i in r['issues']}))}). "
                "I'll send these source columns as context for each flagged field:\n"
                + "\n".join(f"  - {c}" for c in cols_to_send)
                + "\n\nOK to proceed? Or list any column names to exclude, comma-separated "
                "(e.g. for sensitive/PII columns you don't want sent to the LLM)."
            ),
            payload={"columns": cols_to_send, "flagged_count": len(lint_records)},
        )

        excluded = set()
        normalized = str(answer).strip().lower()
        if normalized not in ("", "yes", "y", "ok", "confirm", "looks good"):
            excluded = {c.strip() for c in str(answer).split(",") if c.strip()}

        if excluded:
            for rec in lint_records:
                sr = rec.get("source_row") or {}
                rec["source_row"] = {k: v for k, v in sr.items() if k not in excluded}
            Path(lint_path).write_text(json.dumps(lint_records, indent=2, ensure_ascii=False), encoding="utf-8")
            yield Prompt(
                kind="info",
                message=f"Excluding {sorted(excluded)} from LLM context. Continuing ...",
            )

        fixes_path = str(work_subdir / f"{file_stem}_chat_fixes.json")
        checkpoint_path = str(work_subdir / f"{file_stem}_chat_fixes.checkpoint.json")
        fixup(lint_path, fixes_path, checkpoint_path, format_yaml, model)

    # ── validate ────────────────────────────────────────────────────────────
    from vlmd_lint import lint

    validation_path = str(work_subdir / f"{file_stem}_chat_validation.json")
    lint(converted_path, validation_path, title)

    # ── merge ───────────────────────────────────────────────────────────────
    from vlmd_merge import merge

    merge(
        converted_path, fixes_path, str(vlmd_out), hdp_id, appl_id, title,
        "DataDictionary", format_name, validate=True, file_stem=file_stem,
        input_filename=Path(input_file).name,
    )

    yield Prompt(
        kind="done",
        message=(
            f"Done! Converted {len(json.loads(Path(converted_path).read_text()))} fields "
            f"({len(lint_records)} were flagged and sent through LLM fixup). "
            "Output in " + str(vlmd_out)
        ),
        payload={"output_dir": str(vlmd_out)},
    )


def _terminal_demo(input_file: str, hdp_id: str, model: str, format_yaml: str | None):
    """Drives run_conversational() from the terminal for a live demo."""
    gen = run_conversational(input_file, hdp_id=hdp_id, model=model, format_yaml=format_yaml)
    prompt = next(gen)
    while True:
        print(f"\n🤖 {prompt.message}")
        if prompt.payload:
            print(json.dumps(prompt.payload, indent=2, default=str)[:1500])
        if prompt.kind in ("done", "cancelled", "error"):
            break
        answer = "" if prompt.kind == "info" else input("👤 > ")
        try:
            prompt = gen.send(answer)
        except StopIteration:
            break


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Conversational VLMD pipeline prototype (issue #15)")
    ap.add_argument("--input", required=True)
    ap.add_argument("--hdp-id", default="")
    ap.add_argument("--model", default="azure-gpt-4.1-mini")
    ap.add_argument("--format", default=None, help="Skip detection, use this format YAML directly")
    args = ap.parse_args()
    _terminal_demo(args.input, args.hdp_id, args.model, args.format)

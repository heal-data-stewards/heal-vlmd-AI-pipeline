"""VLMD conversion pipeline — single entry point.

Orchestrates detect → convert → validate → fixup → merge by calling each
module's functions directly (no subprocesses).

Usage:
    python run_pipeline.py --input FILE --hdp-id HDP01258 [OPTIONS]

See README.md or --help for full option list.
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

import yaml

from cli_ui import bold, bold_yellow, print_action_required

PIPELINE_DIR = Path(__file__).parent
WORK_DIR = PIPELINE_DIR / "work"
OUTPUT_DIR = PIPELINE_DIR / "output"


def _banner(text: str):
    print(f"\n=== {text} ===", flush=True)


def _derive_name(input_file: str) -> str:
    """Derive a clean file stem from the input path (spaces → underscores)."""
    return re.sub(r"[\s]+", "_", Path(input_file).stem)


DESCRIPTION_REVIEW_ISSUE = "description_too_short"
_SUGGESTION_PRIORITY = {"flag_for_human": 0, "send_to_llm": 1, "leave_as_is": 2}


def _apply_description_review_gate(
    convert_lint_records: list,
    convert_lint_path: str,
    work_subdir: Path,
    decisions_path: str | None,
    yes: bool,
    model: str,
) -> tuple[list, int | None]:
    """Short/placeholder descriptions are flagged by the converter, but whether
    the LLM should rewrite them is a judgment call that needs context a blind
    heuristic can't see — a foreign-language word ("todo" = "all" in Spanish)
    can look exactly like an English placeholder, and a legitimate short field
    label ("Header") can look exactly like a truncated fragment.

    An LLM triage pass classifies each candidate WITH source-row context
    (table_label, domain, ...) and pre-fills a suggested decision + one-line
    justification. That is only ever a suggestion: a human still has to supply
    a decisions file (--description-review-decisions) — which can simply be
    this same file, reviewed and edited — before anything is sent to the real
    fixup step.

    Returns (possibly-filtered lint records, exit_code). exit_code is None to
    continue the pipeline, or an int to stop here and wait for the decisions file.
    """
    candidates = [r for r in convert_lint_records if DESCRIPTION_REVIEW_ISSUE in r["issues"]]
    if not candidates:
        return convert_lint_records, None

    decisions: dict[str, dict] = {}
    if decisions_path and Path(decisions_path).exists():
        for d in json.loads(Path(decisions_path).read_text(encoding="utf-8")):
            if d.get("decision") in ("send_to_llm", "leave_as_is"):
                decisions[d["name"]] = {
                    "decision": d["decision"],
                    "justification": d.get("justification", ""),
                }

    undecided = [r for r in candidates if r["name"] not in decisions]

    if undecided and yes:
        # Scripted/non-interactive use: default to the non-destructive choice
        # rather than spending LLM calls on triage no one will review.
        for r in undecided:
            decisions[r["name"]] = {"decision": "leave_as_is", "justification": "--yes: not reviewed"}
        print(
            f"  NOTE: --yes set — defaulting {len(undecided)} short/placeholder "
            "description(s) to 'leave_as_is' (no LLM rewrite, no triage run). Pass "
            "--description-review-decisions to control this explicitly.",
            flush=True,
        )
        undecided = []

    review_path = work_subdir / "vlmd_description_review.json"

    if undecided:
        from vlmd_description_triage import triage

        print(f"\n  Running LLM triage on {len(undecided)} short/placeholder description(s) "
              "to suggest which need a rewrite ...", flush=True)
        triage_checkpoint = str(work_subdir / "vlmd_description_triage.checkpoint.json")
        suggestions = triage(undecided, model, checkpoint_path=triage_checkpoint)

        # flag_for_human sorts first (needs the most attention), then send_to_llm,
        # then leave_as_is — and flag_for_human's "decision" is left blank rather
        # than pre-filled, since there is no confident suggestion to accept or reject.
        scored: list[tuple[int, dict]] = []
        counts = {"flag_for_human": 0, "send_to_llm": 0, "leave_as_is": 0}
        for r in candidates:
            if r["name"] in decisions:
                d = decisions[r["name"]]
                suggested = d["decision"]
                justification = d["justification"]
            else:
                s = suggestions.get(r["name"], {})
                suggested = s.get("decision") or "flag_for_human"
                justification = s.get("justification", "")
                counts[suggested] = counts.get(suggested, 0) + 1
            entry = {
                "name": r["name"],
                "description": r["vlmd_field_draft"].get("description", ""),
                "section": r["vlmd_field_draft"].get("section", ""),
                "decision": "" if suggested == "flag_for_human" else suggested,
                "justification": justification,
            }
            scored.append((_SUGGESTION_PRIORITY.get(suggested, 0), entry))

        scored.sort(key=lambda pair: pair[0])
        review_payload = [entry for _, entry in scored]
        review_path.write_text(
            json.dumps(review_payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        print(
            f"\n  {len(undecided)} field(s) triaged — "
            f"{counts['flag_for_human']} flagged for human input, "
            f"{counts['send_to_llm']} suggested send_to_llm, "
            f"{counts['leave_as_is']} suggested leave_as_is.",
            flush=True,
        )

        rerun_cmd = f"--description-review-decisions {review_path}"
        print_action_required(
            "human review needed before LLM fixup",
            [
                "Open " + bold(str(review_path)) + "\n"
                '(flag_for_human entries are listed first, with "decision" left blank —\n'
                "everything else is pre-filled with a suggestion to check)",
                'Fill in every blank "decision" with "send_to_llm" or "leave_as_is";\n'
                "edit any pre-filled suggestion you disagree with too",
                "Re-run with:\n" + bold(rerun_cmd),
            ],
        )
        return convert_lint_records, 3

    # All decided — record the final decisions and strip declined ones from the lint report
    scored = [
        (
            _SUGGESTION_PRIORITY.get(decisions[r["name"]]["decision"], 0),
            {
                "name": r["name"],
                "description": r["vlmd_field_draft"].get("description", ""),
                "section": r["vlmd_field_draft"].get("section", ""),
                "decision": decisions[r["name"]]["decision"],
                "justification": decisions[r["name"]]["justification"],
            },
        )
        for r in candidates
    ]
    scored.sort(key=lambda pair: pair[0])
    review_payload = [entry for _, entry in scored]
    review_path.write_text(
        json.dumps(review_payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    for r in convert_lint_records:
        if decisions.get(r["name"], {}).get("decision") == "leave_as_is" and DESCRIPTION_REVIEW_ISSUE in r["issues"]:
            r["issues"] = [i for i in r["issues"] if i != DESCRIPTION_REVIEW_ISSUE]

    filtered = [r for r in convert_lint_records if r["issues"]]
    Path(convert_lint_path).write_text(
        json.dumps(filtered, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    kept = sum(1 for r in candidates if decisions[r["name"]]["decision"] == "send_to_llm")
    dropped = sum(1 for r in candidates if decisions[r["name"]]["decision"] == "leave_as_is")
    print(
        f"  Description review: {kept} sent to LLM, {dropped} left as-is "
        f"(decisions in {review_path})",
        flush=True,
    )
    return filtered, None


def run(
    input_file: str,
    format_yaml: str | None,
    appl_id: str,
    hdp_id: str,
    title: str,
    study_label: str,
    name: str | None = None,
    model: str = "azure-gpt-4.1-mini",
    skip_llm: bool = False,
    no_detect: bool = False,
    output_dir: Path = OUTPUT_DIR,
    no_confirm: bool = False,
    yes: bool = False,
    dest_dir: Path | None = None,
    description_review_decisions: str | None = None,
    sheet: str | None = None,
) -> int:
    # ── Derive file stem ──────────────────────────────────────────────────────
    file_name = name or _derive_name(input_file)
    file_stem = f"{hdp_id}_{file_name}" if hdp_id else file_name

    # Per-run subdirectories
    run_key = hdp_id if hdp_id else "scratch"
    work_subdir = WORK_DIR / run_key
    work_subdir.mkdir(parents=True, exist_ok=True)

    vlmd_out = output_dir / "vlmd" / file_stem
    vlmd_out.mkdir(parents=True, exist_ok=True)

    input_copy_dir = output_dir / "input"
    input_copy_dir.mkdir(parents=True, exist_ok=True)

    # ── Step 0a: PDF pre-extraction ──────────────────────────────────────────
    # If the input is a PDF, extract variables to an intermediate CSV first,
    # then continue with the normal detect → convert → fixup pipeline on that CSV.
    if Path(input_file).suffix.lower() == ".pdf":
        _banner("Step 0a: PDF extraction")
        from vlmd_pdf import extract as pdf_extract

        extracted_csv = str(work_subdir / f"{Path(input_file).stem}_extracted.csv")
        rc = pdf_extract(input_file, extracted_csv, model_key=model)
        if rc != 0:
            return rc
        print(f"  PDF extraction complete — continuing pipeline on: {extracted_csv}", flush=True)
        input_file = extracted_csv
        # The extracted CSV always uses generic-csv columns — skip format detection.
        if not format_yaml:
            format_yaml = str(PIPELINE_DIR / "formats" / "generic-csv.yaml")

    # ── Step 0b: Excel pre-extraction ─────────────────────────────────────────
    # If the input is an Excel workbook, convert the relevant sheet to an
    # intermediate CSV first, then continue with the normal pipeline on that CSV.
    # Unlike PDF, the resulting CSV can be any known format (REDCap, CDE-style,
    # etc.), so format detection still runs normally on it.
    elif Path(input_file).suffix.lower() in (".xlsx", ".xls"):
        _banner("Step 0b: Excel extraction")
        from vlmd_excel import extract as excel_extract

        extracted_csv = str(work_subdir / f"{Path(input_file).stem}_extracted.csv")
        rc = excel_extract(input_file, extracted_csv, sheet_name=sheet)
        if rc != 0:
            return rc
        print(f"  Excel extraction complete — continuing pipeline on: {extracted_csv}", flush=True)
        input_file = extracted_csv

    # ── Step 0: HEAL platform lookup ─────────────────────────────────────────
    if hdp_id:
        _banner("Step 0: HEAL platform lookup")
        from vlmd_lookup import lookup, confirm_study

        resolved_appl_id, study_info = lookup(hdp_id, appl_id if appl_id != "unknown" else None)

        if resolved_appl_id:
            appl_id = resolved_appl_id
            if title == "HEAL Study Data Dictionary" and study_info.get("study_name"):
                title = study_info["study_name"]

        if study_info and not confirm_study(no_confirm=no_confirm):
            print(f"\n  {bold('Aborted')} — study not confirmed.", flush=True)
            return 1

    print("=" * 44, flush=True)
    print(" VLMD Conversion Pipeline", flush=True)
    print(f" Input:  {input_file}", flush=True)
    print(f" Appl:   {appl_id}  HDP: {hdp_id or 'n/a'}", flush=True)
    print(f" Stem:   {file_stem}", flush=True)
    print(f" Model:  {model}", flush=True)
    print("=" * 44, flush=True)

    # ── Step 1: Format detection ──────────────────────────────────────────────
    if not no_detect and not format_yaml:
        _banner("Step 1: Format detection")
        from vlmd_detect import detect, format_detection_message

        detection = detect(
            input_file,
            formats_dir=str(PIPELINE_DIR / "formats"),
            model_key=model,
            llm_fallback=True,
        )

        detection_path = work_subdir / "vlmd_detection.json"
        detection_path.write_text(
            json.dumps(
                {k: v for k, v in detection.items() if k != "sample_rows"},
                indent=2, ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        if detection["format_name"] is None:
            print(format_detection_message(detection, interactive=False), flush=True)
            
            mapping_path = work_subdir / "vlmd_proposed_mapping.json"
            mapping_path.write_text(
                json.dumps(detection.get("proposed_mapping") or {},
                          indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            columns_arg = ",".join(detection.get("columns", []))
            save_cmd = (
                f"python vlmd_interview.py save \\\n"
                f"  --applid '{appl_id}' \\\n"
                f"  --hdp-id '{hdp_id}' \\\n"
                f"  --source-file '{Path(input_file).name}' \\\n"
                f"  --columns '{columns_arg}' \\\n"
                f"  --mapping-json \"$(cat {mapping_path})\""
            )
            rerun_cmd = (
                f"python run_pipeline.py --input '{input_file}' "
                f"--format formats/{appl_id}.yaml --no-detect ..."
            )
            print_action_required(
                "unrecognized format — a mapping needs review before conversion",
                [
                    "Review the proposed mapping (full detection JSON:\n"
                    + bold(str(detection_path)) + ")",
                    "Correct it if needed by editing\n" + bold(str(mapping_path)),
                    "Save the mapping:\n" + bold(save_cmd),
                    "Re-run with:\n" + bold(rerun_cmd),
                ],
            )
            return 1

        format_yaml = detection["format_yaml_path"]
        print(f"  Using format: {format_yaml}", flush=True)

    elif not format_yaml:
        print("ERROR: --format is required when --no-detect is set", file=sys.stderr)
        return 1

    format_name = yaml.safe_load(Path(format_yaml).read_text(encoding="utf-8")).get(
        "format_name", Path(format_yaml).stem
    )

    # ── Step 2: Convert ───────────────────────────────────────────────────────
    _banner(f"Step 2: Convert  (format: {format_name})")
    from vlmd_convert import convert

    converted_path = str(work_subdir / "vlmd_converted.json")
    convert_lint_path = str(work_subdir / "vlmd_lint_report.json")

    rc = convert(input_file, format_yaml, converted_path, convert_lint_path)
    if rc != 0:
        return rc

    # Load and report converter lint flags
    convert_lint_records = json.loads(Path(convert_lint_path).read_text(encoding="utf-8"))
    if convert_lint_records:
        issue_counts: dict[str, int] = {}
        for rec in convert_lint_records:
            for issue in rec["issues"]:
                issue_counts[issue] = issue_counts.get(issue, 0) + 1
        breakdown = ", ".join(f"{n} {issue}" for issue, n in sorted(issue_counts.items()))
        print(f"\n  {len(convert_lint_records)} field(s) flagged for LLM review "
              f"({breakdown}) — see {convert_lint_path}", flush=True)

    # ── Step 3: Validate ──────────────────────────────────────────────────────
    _banner("Step 3: Validate")
    from vlmd_lint import lint

    validation_report_path = str(work_subdir / "vlmd_validation_report.json")
    lint_exit = lint(converted_path, validation_report_path, title)

    # ── Step 4: LLM fixup ─────────────────────────────────────────────────────
    # Run if converter flagged any fields OR schema validation found errors
    needs_fixup = (lint_exit != 0 or bool(convert_lint_records))
    fixes_path = None
    cleanup_log_path = str(work_subdir / "vlmd_llm_cleanup.json")

    if needs_fixup and not skip_llm:
        convert_lint_records, gate_exit = _apply_description_review_gate(
            convert_lint_records, convert_lint_path, work_subdir,
            description_review_decisions, yes, model,
        )
        if gate_exit is not None:
            return gate_exit
        needs_fixup = (lint_exit != 0 or bool(convert_lint_records))

    if needs_fixup and not skip_llm:
        _banner(f"Step 4: LLM fixup  (model: {model})")
        from vlmd_fixup import fixup

        fixes_path = str(work_subdir / "vlmd_llm_fixes.json")
        checkpoint_path = str(work_subdir / "vlmd_llm_fixes.checkpoint.json")

        if convert_lint_records and lint_exit == 0:
            print(
                f"  Note: schema validation passed but {len(convert_lint_records)} "
                "converter flag(s) need LLM attention.",
                flush=True,
            )

        rc = fixup(
            convert_lint_path,
            fixes_path,
            checkpoint_path,
            format_yaml,
            model,
            cleanup_log_path=cleanup_log_path,
        )
        if rc != 0:
            return rc

    elif needs_fixup and skip_llm:
        print(
            f"  (LLM fixup skipped via --skip-llm; "
            f"{len(convert_lint_records)} flag(s) left unresolved)",
            flush=True,
        )

    # ── Step 5: Merge and write output ────────────────────────────────────────
    _banner("Step 5: Merge and write output")
    from vlmd_merge import merge

    rc = merge(
        converted_path,
        fixes_path,
        str(vlmd_out),
        hdp_id,
        appl_id,
        title,
        study_label,
        format_name,
        validate=True,
        file_stem=file_stem,
        input_filename=Path(input_file).name,
    )

    # ── Copy input file to output/input/ ──────────────────────────────────────
    # Before the rc check, so a validation failure (exit 2) still leaves the
    # full output layout for inspection.
    src_input = Path(input_file)
    if src_input.exists():
        shutil.copy2(src_input, input_copy_dir / src_input.name)
        print(f"  Input  → {input_copy_dir / src_input.name}", flush=True)

    if rc != 0:
        return rc

    # ── Copy to destination repository (optional) ─────────────────────────────
    if dest_dir and hdp_id:
        dest_hdp = dest_dir / hdp_id
        dest_vlmd = dest_hdp / "vlmd" / file_stem
        dest_input = dest_hdp / "input"

        _banner(f"Copying to {dest_dir}")
        dest_vlmd.mkdir(parents=True, exist_ok=True)
        dest_input.mkdir(parents=True, exist_ok=True)

        copied = 0
        for pattern in ("*.vlmd.json", "*.vlmd.csv", "metadata.yaml"):
            for src in vlmd_out.glob(pattern):
                shutil.copy2(src, dest_vlmd / src.name)
                print(f"  {src.name} → {dest_vlmd}", flush=True)
                copied += 1

        if src_input.exists():
            shutil.copy2(src_input, dest_input / src_input.name)
            print(f"  {src_input.name} → {dest_input}", flush=True)

        if copied == 0:
            print("  Nothing to copy.", flush=True)

    elif dest_dir and not hdp_id:
        print("  NOTE: --dest-dir skipped (requires --hdp-id).", flush=True)

    print(f"\n{'=' * 44}", flush=True)
    print(f" Done!  Output in: {vlmd_out}", flush=True)
    if Path(cleanup_log_path).exists():
        print(f" LLM log: {cleanup_log_path}", flush=True)
    print(f"{'=' * 44}", flush=True)
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Convert a data dictionary to HEAL VLMD format.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Standard run — APPL_ID and title fetched from HEAL platform
  python run_pipeline.py --input data_dict.csv --hdp-id HDP01258

  # Control the output filename prefix
  python run_pipeline.py --input data_dict.csv --hdp-id HDP01258 \\
    --name HBCD_datadictionary

  # Copy validated output to a destination repository
  python run_pipeline.py --input data_dict.csv --hdp-id HDP01258 \\
    --dest-dir /path/to/heal-data-dictionaries/data-dictionaries

  # Known format, skip detection
  python run_pipeline.py --input data_dict.csv --hdp-id HDP01258 \\
    --format formats/hbcd.yaml --name HBCD_datadictionary

  # Non-interactive / scripted use (no study confirmation, no stop for description review)
  python run_pipeline.py --input data_dict.csv --hdp-id HDP01258 --no-confirm --yes

  # Skip LLM (deterministic only)
  python run_pipeline.py --input data_dict.csv --hdp-id HDP01258 --skip-llm
""",
    )
    ap.add_argument("--input", required=True, help="Path to input data dictionary (CSV or .dta)")
    ap.add_argument("--format", default=None, help="Format YAML path (skips auto-detection)")
    ap.add_argument("--hdp-id", default="",
                    help="HEAL Data Platform project ID (e.g. HDP01258)")
    ap.add_argument("--appl-id", default="unknown",
                    help="NIH award/application number (auto-fetched if --hdp-id provided)")
    ap.add_argument("--title", default="HEAL Study Data Dictionary",
                    help="Study title (auto-fetched if --hdp-id provided)")
    ap.add_argument("--study-label", default="DataDictionary",
                    help="Short label for metadata (legacy)")
    ap.add_argument("--name", default=None,
                    help="Output filename prefix (default: derived from input filename). "
                         "Final stem: {hdp-id}_{name}. "
                         "Example: --name HBCD_datadictionary → HDP01258_HBCD_datadictionary.vlmd.json")
    ap.add_argument("--model", default="azure-gpt-4.1-mini",
                    help="LLM model key (default: azure-gpt-4.1-mini)")
    ap.add_argument("--output-dir", default=str(OUTPUT_DIR),
                    help="Base output directory. Files go to {output-dir}/vlmd/{stem}/ "
                         "and {output-dir}/input/. Default: output/{hdp-id}/")
    ap.add_argument("--skip-llm", action="store_true", help="Skip LLM fixup step")
    ap.add_argument("--no-detect", action="store_true",
                    help="Skip format detection (requires --format)")
    ap.add_argument("--no-confirm", action="store_true",
                    help="Skip the study confirmation prompt (for scripted/non-interactive use)")
    ap.add_argument("--yes", action="store_true",
                    help="Non-interactive: auto-decide undecided short/placeholder descriptions "
                         "as 'leave_as_is' instead of stopping for human review "
                         "(see --description-review-decisions)")
    ap.add_argument("--dest-dir", default=None,
                    help="Root of destination repository (e.g. heal-data-dictionaries/data-dictionaries). "
                         "Files are copied to {dest-dir}/{hdp-id}/vlmd/{stem}/ and {dest-dir}/{hdp-id}/input/ "
                         "after validation passes.")
    ap.add_argument("--description-review-decisions", default=None,
                    help="Path to a decisions JSON (from a prior run's "
                         "work/{hdp-id}/vlmd_description_review.json) with each entry's "
                         "\"decision\" set to send_to_llm or leave_as_is. Without it, a run "
                         "with undecided short/placeholder descriptions stops and writes "
                         "that file for review (exit code 3).")
    ap.add_argument("--sheet", default=None,
                    help="Sheet name to use for .xlsx/.xls input (default: auto-picks the "
                         "sheet with the most cells). Only relevant for Excel input.")

    args = ap.parse_args()

    # Default output-dir to output/{hdp-id}/ when not explicitly set
    if args.output_dir == str(OUTPUT_DIR) and args.hdp_id:
        output_dir = OUTPUT_DIR / args.hdp_id
    else:
        output_dir = Path(args.output_dir)

    sys.exit(run(
        input_file=args.input,
        format_yaml=args.format,
        appl_id=args.appl_id,
        hdp_id=args.hdp_id,
        title=args.title,
        study_label=args.study_label,
        name=args.name,
        model=args.model,
        skip_llm=args.skip_llm,
        no_detect=args.no_detect,
        output_dir=output_dir,
        no_confirm=args.no_confirm,
        yes=args.yes,
        dest_dir=Path(args.dest_dir) if args.dest_dir else None,
        description_review_decisions=args.description_review_decisions,
        sheet=args.sheet,
    ))


if __name__ == "__main__":
    main()

# AGENTS.md

This file provides guidance to AI coding agents (Claude Code and others) when working with code in this repository.

## What this is

A pipeline that converts research data dictionaries (HBCD CSV, REDCap exports, Stata `.dta`, CDE spreadsheets, Excel, PDF codebooks, arbitrary CSVs) into the HEAL VLMD JSON schema v0.3.2. Flat Python 3.11+ project, no package, no `pyproject.toml`. The `vlmd_*.py` stage modules (except `vlmd_description_triage.py`) and `run_pipeline.py` are both CLI scripts and importable modules; `llm_client.py`, `cli_ui.py` and `vlmd_description_triage.py` are import-only helpers, and `probe_azure.py` is a standalone script that probes endpoints at import time, so don't import it. GitHub repo: `heal-data-stewards/heal-vlmd-AI-pipeline`.

## Commands

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # pyreadstat separately for .dta input
cp .env.template .env                    # only needed for LLM steps

# Smoke test — the only automated check in the repo. Exit 0 = all three examples convert and pass schema validation.
python examples/run_examples.py

# Full pipeline on a real file
python run_pipeline.py --input FILE --hdp-id HDP01258
python run_pipeline.py --input FILE --appl-id 123 --title "X" --skip-llm --no-detect --format formats/hbcd.yaml   # offline, deterministic

# Run one stage in isolation (each script has --help)
python vlmd_detect.py --input FILE --no-llm      # rule-based detection, no credentials
python vlmd_convert.py --input FILE --format formats/hbcd.yaml --output-fields work/x.json --output-lint work/lint.json
python vlmd_lint.py --fields work/x.json --report work/val.json --title "X"

# Conversational prototype (generator with pause points)
python chat_pipeline.py --input demo/demo_dd.csv --hdp-id DEMO001

# Check Azure deployment names / endpoint shape when LLM calls fail
python probe_azure.py
```

There is no test suite and no linter config. `examples/run_examples.py` runs `--skip-llm --no-detect` against the three synthetic CSVs in `examples/` and validates the output; run it after touching any stage. If `healdata_utils` can't be imported, validation fails rather than being skipped, so a broken environment shows up as FAIL (exit 2), not a false PASS. `work/`, `output/` and `examples/output/` are gitignored scratch.

## Architecture

**Stage chain.** `run_pipeline.run()` imports and calls each stage's function directly (no subprocesses): (`vlmd_pdf` / `vlmd_excel` →) (`vlmd_lookup` →) `vlmd_detect` → `vlmd_convert` → `vlmd_lint` → (`vlmd_description_triage` →) `vlmd_fixup` → `vlmd_merge`. PDF/Excel extraction runs first, before the lookup, so it can't use the looked-up APPL_ID or title; the lookup runs only when `--hdp-id` is given. State between stages is JSON files in `work/{hdp-id}/` (`work/scratch/` without `--hdp-id`), so any stage can be re-run standalone from the files the previous one left. `chat_pipeline.py` reuses the same stage functions, reordered as a generator that `yield`s `Prompt` objects at checkpoints and resumes via `.send()`; keep pipeline logic in the stage modules, not in either driver.

**Formats are data, not code.** Each supported input lives in `formats/*.yaml` (column mapping, `levels.format` parser choice, `custom_columns`, `excluded_columns` with reasons, prompt paths, and `detection.signature_columns` with strong/supporting weights). `vlmd_convert.py` interprets the YAML; `vlmd_detect.py` scores every YAML's signature columns against the input header and falls back to an LLM only if nothing clears the threshold. A top score that clears the threshold but is within `AMBIGUITY_GAP` of the runner-up is still returned (with `ambiguous: True`) and `run_pipeline` uses it without further review. Adding a format = adding a YAML (plus optionally a prompt in `prompts/`). `formats/{applid}.yaml` and `formats/HDP*.yaml` files are study-specific mappings saved by `vlmd_interview.py save` after a user confirmed an LLM-proposed mapping; they are legitimate committed artifacts, not clutter.

**Imperative pre-processors for inputs a YAML can't describe.** `vlmd_pdf.py` (pdfplumber + LLM) emits a CSV with `generic-csv.yaml` columns and skips detection. `vlmd_excel.py` (largest-sheet heuristic, `--sheet` override) writes the chosen sheet out as CSV with its header unchanged, so normal format detection runs on it. Both then hand off to the normal chain. `run_pipeline` dispatches on file extension. NDAR is the next candidate for this treatment; `NDAR_CONVERSION_NOTES.md` is a design doc only, nothing in it is implemented.

**The LLM is a repair tool, not the converter.** Conversion is deterministic. Only fields flagged by the converter's lint (`vlmd_lint_report.json`) go to `vlmd_fixup.py`; a schema-validation failure triggers the fixup step but its errors (`vlmd_validation_report.json`) are not passed to it, so they aren't repaired unless the converter also flagged the field. Fixup runs chunked (`MAX_ROWS_PER_CHUNK`) and checkpointed so a re-run resumes. Encoding fixes use `ftfy` locally and are logged to `vlmd_encoding_fixes.json`; only unrecoverable `�` characters become an `encoding_corruption` issue for the LLM. Short/placeholder descriptions are a special gate: `vlmd_description_triage.py` proposes `send_to_llm` / `leave_as_is` / `flag_for_human` per field, the pipeline exits 3 and waits for a human-edited decisions file (`--description-review-decisions`); `--yes` bypasses this by defaulting undecided fields to `leave_as_is`. `--no-confirm` is separate: it only skips the study-confirmation prompt after the lookup, so fully unattended runs need both.

**LLM access goes through `llm_client.py`.** `MODELS` maps a short key (default `azure-gpt-4.1-mini`) to provider + model id + quirks (`supports_temperature`, `use_max_completion_tokens`). Azure AI Foundry is primary; endpoints ending in `/v1` (after stripping a trailing slash) use the plain OpenAI client, classic Azure endpoints use `AzureOpenAI`. `call_llm` and `parse_json_response` are the only entry points the stages use. Adding a model = adding a `MODELS` entry and the README table row.

**Exit codes are an interface.** Bots drive this pipeline by exit code and by the JSON files it writes: `run_pipeline` returns 0 ok; 1 for a general error, including an unknown format (only then is a proposed mapping written to `work/{hdp-id}/vlmd_proposed_mapping.json`), an unconfirmed study, or `--no-detect` without `--format`; 2 when the final output failed schema validation or the validator couldn't run (VLMD files and the input copy are written to `output/` but not copied to `--dest-dir`); 3 when description review is required. A failing PDF/Excel extraction, convert or fixup stage returns its own non-zero code, which `run` passes through unchanged, so treat any other non-zero code as an error. `vlmd_detect` returns 1 for unknown format (an uncaught exception also exits 1); `vlmd_lint` returns 2 for schema errors and 1 if the validator itself fails. Human-facing "ACTION REQUIRED" blocks come from `cli_ui.print_action_required`; keep machine-readable state in files, not in stdout prose.

**Output layout** mirrors the `heal-data-dictionaries` repo convention: `output/{hdp-id}/vlmd/{hdp-id}_{name}/{hdp-id}_{name}.vlmd.{json,csv}` plus `metadata.yaml`, and `output/{hdp-id}/input/` holds a copy of the input, which for PDF and Excel inputs is the intermediate extracted CSV rather than the original file. Without `--hdp-id`, the prefix and the `{hdp-id}` directory are dropped: `output/vlmd/{name}/{name}.vlmd.{json,csv}` and `output/input/`, and `--dest-dir` is skipped. `--output-dir` replaces `output/{hdp-id}/` as the base. `vlmd_merge` validates before returning, and `run_pipeline` copies to `--dest-dir` only when that check passes.

## Documentation

`README.md` is the user manual and should stay in sync with CLI flags, format YAML anatomy, `levels.format` values, the models table, and exit codes. When changing any of those, update the README in the same change.

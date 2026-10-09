"""MCP server exposing the VLMD conversion pipeline as tools for an LLM agent.

Thin wrappers only — every tool here calls directly into the existing
vlmd_*.py step functions at the repo root (detect/excel/pdf/convert/lint/
fixup/merge/interview). No pipeline logic is duplicated, only exposed, same
principle chat_pipeline.py's own docstring states for itself.

This server is stateless: every tool takes file paths in and/or out, exactly
like the functions it wraps. Conversation/session state belongs entirely in
whatever agent calls this server (see vlmd-app/services/ai-pipeline), not
here — keeps this a reusable toolbox for any future MCP client, not just one
specific chat product.

Every file-path argument is resolved on THIS server's filesystem, not the
caller's. In docker-compose that means callers must pass paths under /data
(the volume this container shares with the ai-pipeline chat agent) — a
host path like /Users/... simply won't exist here.

Standalone, networked service (streamable-http), not a stdio subprocess —
independently addressable (e.g. for local debugging via an MCP client or
MCP Inspector), not just from one caller. Run with:

    python -m mcp_server.server

Reads MCP_SERVER_HOST / MCP_SERVER_PORT from the environment (defaults
0.0.0.0:8090), matching the host/port conventions of the sibling wrapper/
ai-pipeline services in docker-compose.yml.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# Repo root (parent of this package) must be importable for `import
# vlmd_detect` etc. to work — true automatically when run as `python -m
# mcp_server.server` from the repo root, but set explicitly too so the
# server also works if started some other way (e.g. a container ENTRYPOINT
# that doesn't cd first).
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mcp.server.mcpserver import MCPServer  # noqa: E402

mcp = MCPServer(
    name="vlmd-pipeline",
    instructions=(
        "Tools for converting a study data dictionary (CSV/Excel/PDF/Stata) "
        "into the HEAL VLMD schema: detect its format, convert it, validate "
        "the result, fix flagged fields with an LLM, and merge/write the "
        "final output. See the 'vlmd-conversion-skill' resource for the "
        "full conversational workflow this toolbox is meant to support."
    ),
)


def _ok(**fields: Any) -> dict:
    """Every tool returns a plain dict, never a bare 0/1/2 exit code —
    that's literally the bug this server exists to not repeat (see
    validate_vlmd/merge_vlmd below for the two places it mattered most)."""
    return {"success": True, **fields}


def _fail(error: str, **fields: Any) -> dict:
    return {"success": False, "error": error, **fields}


# ---------------------------------------------------------------------------
# File info — the one piece of introspection that genuinely doesn't exist
# anywhere else in the codebase (confirmed: no os.path.getsize/Path.stat()
# call anywhere in the vlmd_*.py modules).
# ---------------------------------------------------------------------------

@mcp.tool()
def get_file_info(file_path: str) -> dict:
    """Get basic facts about an uploaded file: size in bytes and extension.

    Call this for simple questions like "how big is this file?" or "what
    kind of file is this?" before running any other tool. For row/column
    counts, column names, or a sample of the data, use detect_format
    instead — it already computes all of that as part of format detection.
    """
    p = Path(file_path)
    if not p.exists():
        return _fail(f"No file at {file_path}")
    stat = p.stat()
    return _ok(
        path=str(p),
        size_bytes=stat.st_size,
        extension=p.suffix.lower(),
        name=p.name,
    )


# ---------------------------------------------------------------------------
# Detect
# ---------------------------------------------------------------------------

@mcp.tool()
def detect_format(file_path: str, llm_fallback: bool = True) -> dict:
    """Detect what format a data dictionary file is in (e.g. REDCap, Stata,
    a known HEAL study format, or something unrecognized).

    This is normally the FIRST tool to call on a newly uploaded file. Its
    result already includes row_count, column_count, columns (the full list
    of column names), and sample_rows (the first 5 rows as dicts) — so it
    also answers side questions like "how many rows/columns does this file
    have?" or "what columns are in it?" without any other tool call. Re-use
    the result you already have from an earlier call in this conversation
    instead of calling this again for the same file.

    If llm_fallback is True (default) and no rule-based format matches
    confidently, an LLM proposes a column mapping (returned as
    proposed_mapping/column_explanations) — present this to the user for
    confirmation before calling save_format_mapping or convert_to_vlmd with
    it.

    Returns: format_name (str|None — None means unrecognized), confidence
    (0-1), method ("extension"|"rule_based"|"llm"|"unknown"), reasoning,
    format_yaml_path (str|None — pass this straight to convert_to_vlmd when
    set), row_count, column_count, columns, sample_rows, scores, ambiguous,
    and when method=="llm": proposed_mapping, column_explanations.
    """
    from vlmd_detect import detect

    result = detect(file_path, llm_fallback=llm_fallback)
    return _ok(**result)


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------

@mcp.tool()
def list_excel_sheets(xlsx_path: str) -> dict:
    """List every sheet in an Excel workbook with its row/column count.

    Call this before extract_excel_sheet when a workbook has more than one
    sheet, so the user can confirm which one to use (or answer "how many
    sheets does this have?"). Read-only — doesn't modify or extract anything.
    """
    from vlmd_excel import list_sheets

    sheets = list_sheets(xlsx_path)
    return _ok(sheets=[
        {"name": name, "row_count": rows, "column_count": cols}
        for name, rows, cols in sheets
    ])


@mcp.tool()
def extract_excel_sheet(xlsx_path: str, output_csv: str, sheet_name: str | None = None) -> dict:
    """Extract one sheet of an Excel workbook to a CSV file, so it can flow
    through detect_format/convert_to_vlmd like any other CSV.

    If sheet_name is omitted and the workbook has multiple sheets, the
    largest sheet (by row x column count) is picked automatically — call
    list_excel_sheets first and ask the user to confirm if that default
    might be wrong for this file.
    """
    from vlmd_excel import extract

    rc = extract(xlsx_path, output_csv, sheet_name=sheet_name)
    if rc != 0:
        return _fail(
            "Could not extract a sheet — check the file has at least one "
            "non-empty sheet with a header row, and that sheet_name (if "
            "given) actually exists in this workbook."
        )
    return _ok(output_csv=output_csv)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

@mcp.tool()
def extract_pdf_table(pdf_path: str, output_csv: str) -> dict:
    """Extract a data dictionary's variable table out of a PDF into a CSV,
    using an LLM to parse each page's table structure. Can take a while for
    multi-page PDFs — let the user know before calling this.

    The resulting CSV always uses the generic-csv column shape (name, label,
    choices, section) — pass format_yaml="formats/generic-csv.yaml" to
    convert_to_vlmd afterward rather than running detect_format on it (format
    detection on an already-known-shape file is redundant).
    """
    from vlmd_pdf import extract

    rc = extract(pdf_path, output_csv)
    if rc != 0:
        return _fail(
            "Could not extract a usable table from this PDF — it may not "
            "contain a recognizable variable table, or pdfplumber couldn't "
            "read it."
        )
    return _ok(output_csv=output_csv, format_yaml="formats/generic-csv.yaml")


# ---------------------------------------------------------------------------
# Convert
# ---------------------------------------------------------------------------

@mcp.tool()
def convert_to_vlmd(input_path: str, format_yaml: str, output_fields: str, output_lint: str) -> dict:
    """Convert a data dictionary file to VLMD fields using a confirmed format
    mapping (a format_yaml path — from detect_format's format_yaml_path, or
    from save_format_mapping if the format was unrecognized and a mapping
    was just confirmed with the user).

    Writes output_fields (the VLMD field records) and output_lint (fields
    flagged for LLM review — missing/too-short descriptions, unrecognized
    types, encoding corruption). ALWAYS call validate_vlmd on output_fields
    right after this, even if output_lint is empty — convert_to_vlmd's own
    success only means "a name column was found for every row," not that
    the output is schema-valid.
    """
    from vlmd_convert import convert

    rc = convert(input_path, format_yaml, output_fields, output_lint)
    if rc != 0:
        return _fail(
            "Conversion failed — the format mapping doesn't identify a "
            "name column for this file's rows, so there's nothing to build "
            "a field from. Check the mapping's name_column against this "
            "file's actual columns."
        )
    import json
    lint_records = json.loads(Path(output_lint).read_text(encoding="utf-8"))
    return _ok(
        output_fields=output_fields,
        output_lint=output_lint,
        flagged_field_count=len(lint_records),
    )


# ---------------------------------------------------------------------------
# Validate — THE fix for "solidify the linting": a structured, impossible-
# to-silently-discard result, where the old code path computed this and
# then never read it.
# ---------------------------------------------------------------------------

@mcp.tool()
def validate_vlmd(fields_path: str, report_path: str, title: str = "HEAL Study Data Dictionary") -> dict:
    """Validate a VLMD fields JSON file against the HEAL VLMD schema.

    YOU MUST CALL THIS before telling the user a conversion succeeded, and
    AGAIN on the final merged output after merge_vlmd (merge_vlmd already
    re-validates internally, but always read its returned valid/errors
    fields too — never say "done" without having actually looked at a real
    valid=True/False result from this tool or from merge_vlmd's return
    value. Saying a conversion succeeded without checking this is exactly
    the bug this tool exists to prevent.

    Returns: valid (bool), error_count (int), errors (list of error strings,
    truncated to 10), truncated (bool — True if there were more than 10),
    report_path (where the full untruncated report was written).
    """
    from vlmd_lint import lint

    rc = lint(fields_path, report_path, title)
    import json
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    errors = report.get("errors", [])
    return _ok(
        valid=(rc == 0),
        error_count=len(errors),
        errors=errors[:10],
        truncated=len(errors) > 10,
        report_path=report_path,
    )


# ---------------------------------------------------------------------------
# Fixup
# ---------------------------------------------------------------------------

@mcp.tool()
def fixup_flagged_fields(
    lint_path: str,
    output_path: str,
    checkpoint_path: str,
    format_yaml: str | None = None,
    excluded_columns: list[str] | None = None,
) -> dict:
    """Send fields flagged by convert_to_vlmd (missing/short descriptions,
    unrecognized types, encoding issues) to an LLM to fix, using nearby
    source-row context.

    CONFIRMATION REQUIRED: tell the user which source columns will be sent
    as LLM context (derive this from the lint records' source_row keys,
    filtered to what the format mapping actually uses) and let them exclude
    any columns (e.g. for PII) via excluded_columns before calling this —
    do not call this tool without having done that first.

    Resumable: safe to retry after a failure, re-run with the same
    checkpoint_path to pick up where it left off rather than starting over.

    Returns: output_path (fixed field records, ready for merge_vlmd's
    fixes_path argument), fixed_count.
    """
    from vlmd_fixup import fixup
    from llm_client import DEFAULT_MODEL

    lint_path_obj = Path(lint_path)
    if excluded_columns:
        import json

        records = json.loads(lint_path_obj.read_text(encoding="utf-8"))
        for rec in records:
            sr = rec.get("source_row") or {}
            rec["source_row"] = {k: v for k, v in sr.items() if k not in excluded_columns}
        lint_path_obj.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")

    rc = fixup(lint_path, output_path, checkpoint_path, format_yaml, DEFAULT_MODEL)
    if rc != 0:
        return _fail("Fixup hit an unrecoverable error on one chunk — check the checkpoint file and retry.")
    import json
    fixed = json.loads(Path(output_path).read_text(encoding="utf-8"))
    return _ok(output_path=output_path, fixed_count=len(fixed))


# ---------------------------------------------------------------------------
# Merge — final write, and the SECOND place a real validation result used
# to be silently discarded (chat_pipeline.py called merge(..., validate=True)
# but never looked at the return code).
# ---------------------------------------------------------------------------

@mcp.tool()
def merge_vlmd(
    converted_path: str,
    output_dir: str,
    hdp_id: str,
    appl_id: str,
    title: str,
    format_name: str,
    fixes_path: str | None = None,
    study_label: str = "DataDictionary",
    file_stem: str | None = None,
    input_filename: str | None = None,
) -> dict:
    """Merge any LLM fixes onto the converted fields and write the final
    VLMD output (.vlmd.json, .vlmd.csv, metadata.yaml) to output_dir.

    CONFIRMATION REQUIRED: this is the final write — confirm with the user
    before calling it, same as fixup_flagged_fields.

    Always re-validates the final merged document. valid=False in the
    return means the final document is NOT schema-valid even though files
    were written (for inspection) — you MUST tell the user this plainly and
    explain what to fix; never describe this as a successful conversion.
    This is the other half of "solidify the linting," alongside
    validate_vlmd — this tool's own internal validation used to be computed
    and then thrown away by the caller.

    Returns: valid (bool), output_dir, json_path, csv_path.
    """
    from vlmd_merge import merge

    rc = merge(
        converted_path, fixes_path, output_dir, hdp_id, appl_id, title,
        study_label, format_name, validate=True,
        file_stem=file_stem, input_filename=input_filename,
    )
    stem = file_stem or f"{appl_id}_{study_label.replace(' ', '_')}"
    out = Path(output_dir)
    return _ok(
        valid=(rc == 0),
        output_dir=output_dir,
        json_path=str(out / f"{stem}.vlmd.json"),
        csv_path=str(out / f"{stem}.vlmd.csv"),
    )


# ---------------------------------------------------------------------------
# Save a confirmed format mapping
# ---------------------------------------------------------------------------

@mcp.tool()
def save_format_mapping(
    applid: str,
    mapping: dict,
    hdp_id: str = "",
    source_file: str = "",
    columns: list[str] | None = None,
) -> dict:
    """Save a confirmed column mapping (from detect_format's proposed_mapping,
    after the user has reviewed and approved it) as a reusable format YAML,
    so this study's format is recognized automatically next time.

    Only call this after the user has explicitly confirmed the proposed
    mapping is correct — never save an unconfirmed guess.

    Returns: format_yaml (the saved path — pass this to convert_to_vlmd).
    """
    from vlmd_interview import save_format

    path = save_format(applid, mapping, hdp_id=hdp_id, source_file=source_file, columns=columns)
    return _ok(format_yaml=path)


# ---------------------------------------------------------------------------
# Skill — the conversational-workflow instructions, delivered as an MCP
# resource (not a bare file path the agent has to separately know about).
# Content drafted alongside the chat agent that consumes it (Phase 2).
# ---------------------------------------------------------------------------

@mcp.resource("skill://vlmd-conversion")
def vlmd_conversion_skill() -> str:
    """The system-prompt-style instructions for conversationally driving
    this toolbox end to end."""
    return (Path(__file__).parent / "skill.md").read_text(encoding="utf-8")


def main() -> None:
    host = os.environ.get("MCP_SERVER_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_SERVER_PORT", "8090"))
    mcp.run(transport="streamable-http", host=host, port=port, streamable_http_path="/mcp")


if __name__ == "__main__":
    main()

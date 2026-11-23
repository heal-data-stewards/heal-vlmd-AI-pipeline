"""PDF data dictionary extractor for the VLMD conversion pipeline.

Extracts variable/field definitions from PDF codebooks using pdfplumber for
content extraction and an LLM for structured parsing.

The output CSV uses column names (name, label, choices, section) that the
generic-csv format YAML recognises automatically, so the normal
detect → convert → fixup pipeline handles the rest.

Usage (standalone):
    python vlmd_pdf.py --input codebook.pdf --output work/extracted.csv
    python vlmd_pdf.py --input codebook.pdf --output work/extracted.csv --model azure-gpt-4.1-mini

Called automatically by run_pipeline.py when --input ends in .pdf.
"""

import argparse
import csv
import json
import sys
from pathlib import Path

from llm_client import DEFAULT_MODEL, call_llm, parse_json_response

PROMPTS_DIR = Path(__file__).parent / "prompts"

# How many pages to send to the LLM in one batch.
# Qualtrics codebooks are dense; 4-5 pages per call keeps prompt size manageable.
PAGES_PER_BATCH = 4

# A short identifier cell: 2-15 chars, not all whitespace.
# Used to decide whether a row likely contains a variable.
_MIN_NAME_LEN = 2
_MAX_NAME_LEN = 15


# ---------------------------------------------------------------------------
# PDF content extraction
# ---------------------------------------------------------------------------


def _extract_page_rows(page) -> list[list]:
    """Return a flat list of table rows from a pdfplumber page object."""
    rows = []
    for table in page.extract_tables():
        for row in table:
            # Normalise cells: keep None, strip strings, collapse internal newlines
            cleaned = []
            for cell in row:
                if cell is None:
                    cleaned.append(None)
                else:
                    cleaned.append(str(cell).strip())
            rows.append(cleaned)
    return rows


def extract_all_pages(pdf_path: str) -> list[list[list]]:
    """
    Return one list of row-lists per page.
    Requires pdfplumber.
    """
    try:
        import pdfplumber
    except ImportError:
        raise ImportError(
            "pdfplumber is required for PDF extraction.\nInstall it with:  pip install pdfplumber"
        )

    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        print(f"  PDF: {len(pdf.pages)} pages", flush=True)
        for page in pdf.pages:
            pages.append(_extract_page_rows(page))
    return pages


def _has_variable_candidate(rows: list[list]) -> bool:
    """Quick heuristic: does this batch have any row that might contain a variable name?"""
    for row in rows:
        for cell in row[:3]:
            if cell and _MIN_NAME_LEN <= len(cell) <= _MAX_NAME_LEN and cell.strip():
                return True
    return False


# ---------------------------------------------------------------------------
# LLM parsing
# ---------------------------------------------------------------------------


def _parse_batch(batch_rows: list[list[list]], model_key: str) -> list[dict]:
    """
    Send a batch of page row-lists to the LLM and return parsed variable records.
    batch_rows: list of pages, each page is a list of row-lists.
    """
    prompt_template = (PROMPTS_DIR / "pdf_extract_prompt.md").read_text(encoding="utf-8")

    # Flatten batch into a single array of rows for the prompt
    flat_rows: list[list] = []
    for page_rows in batch_rows:
        flat_rows.extend(page_rows)

    if not _has_variable_candidate(flat_rows):
        return []

    user_msg = (
        f"{prompt_template}\n\n"
        f"## Raw table rows ({len(flat_rows)} total)\n\n"
        f"```json\n{json.dumps(flat_rows, ensure_ascii=False)}\n```"
    )

    raw, ok, err = call_llm(
        system="You are a data dictionary parsing assistant. Return only valid JSON.",
        user=user_msg,
        model_key=model_key,
        max_tokens=4096,
        temperature=0.0,
    )

    if not ok:
        print(f"    WARNING: LLM call failed — {err}", flush=True)
        return []

    if not raw or not raw.strip():
        print("    WARNING: LLM returned empty response — skipping batch", flush=True)
        return []

    try:
        records = parse_json_response(raw)
        if not isinstance(records, list):
            print("    WARNING: LLM returned non-list JSON — skipping batch", flush=True)
            return []
        return records
    except (json.JSONDecodeError, ValueError) as e:
        print(f"    WARNING: Could not parse LLM response — {e}", flush=True)
        return []


# ---------------------------------------------------------------------------
# Deduplication and merge
# ---------------------------------------------------------------------------


def _merge_records(records: list[dict]) -> list[dict]:
    """
    Merge duplicate variable names that appear across page boundaries.
    Later choices are appended to earlier ones.
    """
    seen: dict[str, dict] = {}
    order: list[str] = []

    for rec in records:
        name = (rec.get("name") or "").strip()
        if not name:
            continue
        if name in seen:
            # Append any new choices to the existing record
            existing_choices = seen[name].get("choices", "")
            new_choices = rec.get("choices", "")
            if new_choices and new_choices not in existing_choices:
                seen[name]["choices"] = (
                    f"{existing_choices} | {new_choices}" if existing_choices else new_choices
                )
        else:
            seen[name] = {
                "name": name,
                "label": (rec.get("label") or "").strip(),
                "choices": (rec.get("choices") or "").strip(),
                "section": (rec.get("section") or "").strip(),
            }
            order.append(name)

    merged = [seen[n] for n in order]
    _forward_fill_sections(merged)
    return merged


def _forward_fill_sections(records: list[dict]) -> None:
    """
    Fill blank `section` values with the nearest preceding non-blank section.

    Section headers are parsed per LLM batch (a few pages at a time) with no
    memory of the previous batch, so a section active at a batch boundary is
    frequently lost for the first few variables of the next batch. Since
    records are already in document order, a simple forward-fill recovers
    the correct section for the vast majority of these gaps.
    """
    last_section = ""
    for rec in records:
        if rec["section"]:
            last_section = rec["section"]
        elif last_section:
            rec["section"] = last_section


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def extract(pdf_path: str, output_csv: str, model_key: str = DEFAULT_MODEL) -> int:
    """
    Extract variables from a PDF codebook and write a CSV suitable for the VLMD pipeline.

    Returns 0 on success, 1 on failure.
    """
    print(f"Extracting PDF: {pdf_path}", flush=True)

    try:
        pages = extract_all_pages(pdf_path)
    except ImportError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"ERROR reading PDF: {e}", file=sys.stderr)
        return 1

    all_records: list[dict] = []
    total_batches = (len(pages) + PAGES_PER_BATCH - 1) // PAGES_PER_BATCH

    for batch_idx in range(total_batches):
        start = batch_idx * PAGES_PER_BATCH
        end = min(start + PAGES_PER_BATCH, len(pages))
        batch = pages[start:end]
        print(
            f"  Parsing pages {start + 1}–{end} / {len(pages)} (batch {batch_idx + 1}/{total_batches}) ...",
            flush=True,
        )
        records = _parse_batch(batch, model_key)
        print(f"    → {len(records)} variable(s) found", flush=True)
        all_records.extend(records)

    merged = _merge_records(all_records)
    print(f"\n  Total unique variables extracted: {len(merged)}", flush=True)

    if not merged:
        print(
            "ERROR: No variables extracted from PDF — check the file and model output.",
            file=sys.stderr,
        )
        return 1

    out_path = Path(output_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["name", "label", "choices", "section"])
        writer.writeheader()
        writer.writerows(merged)

    print(f"  Extracted CSV → {out_path}  ({len(merged)} rows)", flush=True)
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(
        description="Extract a data dictionary from a PDF codebook to CSV."
    )
    ap.add_argument("--input", required=True, help="Path to input PDF file")
    ap.add_argument("--output", required=True, help="Path for the output CSV file")
    ap.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"LLM model key (default: {DEFAULT_MODEL})"
    )
    args = ap.parse_args()

    sys.exit(extract(args.input, args.output, model_key=args.model))


if __name__ == "__main__":
    main()

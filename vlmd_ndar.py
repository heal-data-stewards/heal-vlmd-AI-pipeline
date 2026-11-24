"""NDAR/NDA data dictionary extractor for the VLMD conversion pipeline.

Converts NDA "Data Structure Definitions" exports (the fixed 9-column
ElementName/DataType/Size/Required/Condition/ElementDescription/ValueRange/
Notes/Aliases schema) directly to VLMD-shaped intermediate CSVs — one per
NDA structure ("form") — skipping the old NDAR -> REDCap-shaped-CSV ->
VLMD round trip (see NDAR_CONVERSION_NOTES.md for why).

Handles both raw-export shapes:
  - a directory of single-structure `{short_name}_definitions.xlsx` files
  - one combined multi-sheet workbook, one sheet per structure, each sheet
    named `{short_name}_definitions`

The hard part is that DataType + ValueRange + Notes together decide the
final VLMD type/choices *per row*, polymorphically — that logic lives here
in derive_field(); formats/ndar_intermediate.yaml that consumes this
extractor's output is deliberately thin (near-identity mapping).

Usage (standalone):
    python vlmd_ndar.py --input ndar_workbook.xlsx --output-dir work/ndar_extracted
    python vlmd_ndar.py --input OriginalDataDictionaries/HDP00201/ --output-dir work/ndar_extracted

Each form is also convertible end-to-end via vlmd_ndar_pipeline (see
convert_workbook / the --run-pipeline CLI flag), which calls run_pipeline.run()
once per form with formats/ndar_intermediate.yaml and --no-detect.
"""
import argparse
import csv
import re
import sys
from pathlib import Path

import pandas as pd

NDA_COLUMNS = [
    "ElementName", "DataType", "Size", "Required", "Condition",
    "ElementDescription", "ValueRange", "Notes", "Aliases",
]

OUTPUT_COLUMNS = ["name", "label", "section", "type", "choices", "minimum", "maximum",
                  "required", "aliases"]

# \W+ (one-or-more non-word chars) collapses straight to a single "_" in one
# pass — deliberately NOT re.sub(r"\W", "_", ...) followed by a separate
# "_{2,}" -> "_" collapse: that two-pass version also collapsed genuine
# pre-existing double underscores in NDA's own element names (underscore is
# \w, untouched by the \W pass), which silently merged two different ache01
# elements ("ace__q6" and "ace_q6") into one name — confirmed against NDA's
# own structured datastructure endpoint, which gives each a distinct
# dataElementId.
_clean_name = lambda name: re.sub(r"\W+", "_", name.lower().strip()).strip("_")

# A handful of recurring ValueRange/Notes strings that don't fit the general
# key=label parsing rule (bare semicolon-separated lists with no explicit
# codes, where the "code" is actually embedded in the label text itself).
# Flagged in NDAR_CONVERSION_NOTES.md as needing ad hoc handling — this is
# the "~5 entries" the original notebook hardcoded; kept minimal on purpose.
NDAR_VALUE_OVERRIDES: dict[str, dict[str, str]] = {
    "Yes;No": {"1": "Yes", "0": "No"},
}

_RANGE_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*::\s*(-?\d+(?:\.\d+)?)\s*$")
_RANGE_WITH_EXTRA_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*::\s*(-?\d+(?:\.\d+)?)\s*;\s*(.+)$")


def _is_blank(v) -> bool:
    return v is None or (isinstance(v, float) and v != v) or (isinstance(v, str) and not v.strip())


def _clean(v) -> str:
    return "" if _is_blank(v) else str(v).strip()


def _looks_like_kv_list(text: str) -> bool:
    """True if text has at least one 'key=label' pair (';'-separated)."""
    return bool(text) and "=" in text


def _parse_key_equals_label(text: str) -> dict[str, str]:
    """Parse 'key=label;key=label' (or 'key=mid=label' triples, middle dropped)."""
    pairs: dict[str, str] = {}
    if not text:
        return pairs
    for chunk in text.split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        parts = [p.strip() for p in chunk.split("=")]
        key = parts[0]
        label = parts[-1]
        if key:
            pairs[key] = label or key
    return pairs


def _parse_bare_list(text: str) -> dict[str, str]:
    """ 'Male;Female;Other' with no explicit codes -> identity enum."""
    items = [p.strip() for p in text.split(";") if p.strip()]
    return {item: item for item in items}


def _best_kv(notes: str, value_range: str) -> dict[str, str]:
    """Parse key=label pairs from Notes and ValueRange, keep whichever is richer.

    NDAR_CONVERSION_NOTES.md: "the two sources sometimes disagree/need
    reconciling when one has more entries than the other."
    """
    notes_kv = _parse_key_equals_label(notes) if _looks_like_kv_list(notes) else {}
    vr_kv = _parse_key_equals_label(value_range) if _looks_like_kv_list(value_range) else {}
    return vr_kv if len(vr_kv) > len(notes_kv) else notes_kv


def _choices_str(kv: dict[str, str]) -> str:
    return "|".join(f"{k},{v}" for k, v in kv.items())


def derive_field(data_type: str, value_range, notes) -> dict:
    """Decide VLMD type/choices/min/max from DataType + ValueRange + Notes.

    Returns {"type", "choices", "minimum", "maximum", "note_suffix", "free_text_notes"}.

    free_text_notes carries Notes verbatim whenever it wasn't consumed as an
    enum/choices source (i.e. it doesn't look like a 'key=label' list and isn't
    one of the NDAR_VALUE_OVERRIDES keys) — real source content (scoring
    formulas, examples, caveats) that would otherwise be silently dropped.
    The caller appends it to the label as-is; this module never paraphrases
    or invents description text, only combines verbatim source columns.
    """
    out = {"type": "string", "choices": "", "minimum": "", "maximum": "", "note_suffix": "",
           "free_text_notes": ""}
    dt = (data_type or "").strip()
    vr = _clean(value_range)
    nt = _clean(notes)

    if nt and not _looks_like_kv_list(nt) and nt not in NDAR_VALUE_OVERRIDES:
        out["free_text_notes"] = nt

    if vr in NDAR_VALUE_OVERRIDES or nt in NDAR_VALUE_OVERRIDES:
        kv = NDAR_VALUE_OVERRIDES.get(vr) or NDAR_VALUE_OVERRIDES.get(nt)
        out["type"] = "string"
        out["choices"] = _choices_str(kv)
        return out

    if dt == "GUID":
        out["type"] = "string"
    elif dt == "Date":
        out["type"] = "date"
    elif dt == "Float":
        out["type"] = "number"
    elif dt == "Integer":
        kv = _best_kv(nt, vr)
        if kv:
            # Type left as "string" — vlmd_convert.py auto-upgrades to
            # "integer" once it sees every enum value parses as an int.
            out["choices"] = _choices_str(kv)
        else:
            m = _RANGE_RE.match(vr)
            m_extra = _RANGE_WITH_EXTRA_RE.match(vr)
            if m:
                out["type"] = "integer"
                out["minimum"], out["maximum"] = m.group(1), m.group(2)
            elif m_extra:
                out["type"] = "integer"
                out["minimum"], out["maximum"] = m_extra.group(1), m_extra.group(2)
                out["note_suffix"] = f" (additional allowed value(s): {m_extra.group(3)})"
            else:
                out["type"] = "integer"
    else:  # String, or an unrecognized DataType — fall back to text/enum rules
        kv = _best_kv(nt, vr)
        if kv:
            out["choices"] = _choices_str(kv)
        elif vr and ";" in vr and "=" not in vr and "=" not in nt:
            out["choices"] = _choices_str(_parse_bare_list(vr))
        else:
            out["type"] = "string"

    return out


def _clean_label(element_description: str) -> str:
    # Recurring NDA export mojibake: "??" where the source meant an apostrophe.
    desc = _clean(element_description).replace("??", "'")
    return desc.strip('"').strip()


def _row_to_record(row: dict) -> dict:
    name = _clean_name(_clean(row.get("ElementName", "")))
    derived = derive_field(row.get("DataType"), row.get("ValueRange"), row.get("Notes"))
    label = _clean_label(row.get("ElementDescription", ""))
    # Verbatim concatenation only — no paraphrasing. Notes is real source content
    # (scoring formulas, examples, caveats) that's otherwise lost entirely.
    if derived["free_text_notes"]:
        label = f"{label} — {derived['free_text_notes']}" if label else derived["free_text_notes"]
    label += derived["note_suffix"]
    return {
        "name": name,
        "label": label,
        "type": derived["type"],
        "choices": derived["choices"],
        "minimum": derived["minimum"],
        "maximum": derived["maximum"],
        "required": _clean(row.get("Required", "")),
        "aliases": _clean(row.get("Aliases", "")),
    }


def _select_nda_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only the known NDA columns (by name), tolerating extra blank
    columns some exports insert (pandas renames blank headers to "Unnamed: N")."""
    cols = {c: df[c] for c in NDA_COLUMNS if c in df.columns}
    missing = [c for c in NDA_COLUMNS if c not in df.columns]
    if missing:
        print(f"    NOTE: missing expected column(s) {missing} — treated as blank", flush=True)
    return pd.DataFrame(cols)


def _read_definitions_file(f: Path) -> pd.DataFrame:
    """Read one single-structure definitions file — .xlsx or the NDA API's .csv export."""
    if f.suffix.lower() == ".csv":
        return pd.read_csv(f, dtype=str, keep_default_na=False)
    return pd.read_excel(f, sheet_name=0, dtype=str)


def iter_forms(input_path: str):
    """Yield (form_name, DataFrame) pairs for every NDA structure found at input_path.

    input_path can be a directory of `*_definitions.xlsx`/`.csv` files (the
    latter matching the NDA API's own `.../datastructure/{short_name}/csv`
    export shape), or a single multi-sheet workbook.
    """
    path = Path(input_path)
    if path.is_dir():
        files = sorted(path.glob("*_definitions.xlsx")) + sorted(path.glob("*_definitions.csv"))
        if not files:
            files = sorted(path.glob("*.xlsx")) + sorted(path.glob("*.csv"))
        if not files:
            print(f"ERROR: no .xlsx/.csv files found in {path}", file=sys.stderr)
            return
        for f in files:
            form_name = f.stem
            if form_name.endswith("_definitions"):
                form_name = form_name[: -len("_definitions")]
            df = _read_definitions_file(f)
            yield form_name, _select_nda_columns(df)
    elif path.suffix.lower() == ".csv":
        form_name = path.stem
        if form_name.endswith("_definitions"):
            form_name = form_name[: -len("_definitions")]
        df = _read_definitions_file(path)
        yield form_name, _select_nda_columns(df)
    else:
        sheets = pd.read_excel(path, sheet_name=None, dtype=str)
        for sheet_name, df in sheets.items():
            form_name = sheet_name
            if form_name.endswith("_definitions"):
                form_name = form_name[: -len("_definitions")]
            yield form_name, _select_nda_columns(df)


def extract_form(form_name: str, df: pd.DataFrame, output_csv: str) -> int:
    if "ElementName" not in df.columns:
        print(f"  ERROR: '{form_name}' has no ElementName column — skipping", file=sys.stderr)
        return 1

    records = []
    for _, row in df.iterrows():
        row_dict = row.to_dict()
        if _is_blank(row_dict.get("ElementName")):
            continue
        records.append(_row_to_record(row_dict))

    if not records:
        print(f"  ERROR: '{form_name}' produced no rows", file=sys.stderr)
        return 1

    out_path = Path(output_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        for rec in records:
            rec["section"] = form_name
            writer.writerow(rec)

    n_enum = sum(1 for r in records if r["choices"])
    n_range = sum(1 for r in records if r["minimum"] or r["maximum"])
    print(f"  {form_name}: {len(records)} field(s)  "
          f"({n_enum} enum, {n_range} range)  -> {out_path}", flush=True)
    return 0


def extract_workbook(input_path: str, output_dir: str) -> list[tuple[str, str]]:
    """Extract every form found at input_path to {output_dir}/{form_name}.ndar_extracted.csv.

    Returns a list of (form_name, csv_path) for every form successfully extracted.
    """
    results = []
    for form_name, df in iter_forms(input_path):
        clean_form = _clean_name(form_name)
        out_csv = str(Path(output_dir) / f"{clean_form}.ndar_extracted.csv")
        rc = extract_form(form_name, df, out_csv)
        if rc == 0:
            results.append((clean_form, out_csv))
    return results


def convert_workbook(
    input_path: str,
    hdp_id: str,
    appl_id: str,
    title: str,
    output_dir: str,
    skip_llm: bool,
    model: str,
    dest_dir: str | None = None,
) -> int:
    """Extract every form at input_path, then run each through the normal
    convert -> validate -> fixup -> merge pipeline (formats/ndar_intermediate.yaml,
    detection skipped since the extractor already declares the format)."""
    from run_pipeline import run as run_pipeline

    extract_dir = str(Path(output_dir) / "ndar_extracted" / (hdp_id or "scratch"))
    forms = extract_workbook(input_path, extract_dir)
    if not forms:
        print("ERROR: no forms extracted", file=sys.stderr)
        return 1

    format_yaml = str(Path(__file__).parent / "formats" / "ndar_intermediate.yaml")
    failures = []
    for form_name, csv_path in forms:
        print(f"\n--- Converting form: {form_name} ---", flush=True)
        rc = run_pipeline(
            input_file=csv_path,
            format_yaml=format_yaml,
            appl_id=appl_id,
            hdp_id=hdp_id,
            title=title,
            study_label="DataDictionary",
            name=form_name,
            model=model,
            skip_llm=skip_llm,
            no_detect=True,
            output_dir=Path(output_dir),
            no_confirm=True,
            dest_dir=Path(dest_dir) if dest_dir else None,
        )
        if rc != 0:
            failures.append(form_name)

    print(f"\n{'=' * 44}", flush=True)
    print(f" NDAR conversion: {len(forms) - len(failures)}/{len(forms)} form(s) succeeded",
          flush=True)
    if failures:
        print(f" Failed: {failures}", flush=True)
    print(f"{'=' * 44}", flush=True)
    return 1 if failures else 0


def main():
    ap = argparse.ArgumentParser(
        description="Extract NDA data structure definitions to VLMD-shaped intermediate CSV(s), "
                     "optionally running the full convert/validate/fixup/merge pipeline on each."
    )
    ap.add_argument("--input", required=True,
                     help="Path to a workbook (.xlsx) or a directory of *_definitions.xlsx files")
    ap.add_argument("--output-dir", required=True,
                     help="Extraction-only: directory to write one {form}.ndar_extracted.csv per "
                          "structure. Full-pipeline: base output dir (see run_pipeline.py).")
    ap.add_argument("--run-pipeline", action="store_true",
                     help="Also run each extracted form through convert/validate/fixup/merge, "
                          "producing real VLMD output under --output-dir/vlmd/.")
    ap.add_argument("--hdp-id", default="", help="HEAL Data Platform project ID (e.g. HDP00201)")
    ap.add_argument("--appl-id", default="unknown")
    ap.add_argument("--title", default="HEAL Study Data Dictionary")
    ap.add_argument("--skip-llm", action="store_true")
    ap.add_argument("--model", default="azure-gpt-4.1-mini")
    ap.add_argument("--dest-dir", default=None,
                     help="Root of destination repository to copy validated output into")
    args = ap.parse_args()

    if args.run_pipeline:
        sys.exit(convert_workbook(
            args.input, args.hdp_id, args.appl_id, args.title,
            args.output_dir, args.skip_llm, args.model, args.dest_dir,
        ))

    print(f"Extracting NDAR definitions: {args.input}", flush=True)
    results = extract_workbook(args.input, args.output_dir)
    print(f"\nExtracted {len(results)} form(s) to {args.output_dir}", flush=True)
    sys.exit(0 if results else 1)


if __name__ == "__main__":
    main()

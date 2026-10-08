"""Merge LLM fixes into converted fields and write final VLMD output.

Generic — works for any format. Stem naming uses appl_id + a configurable
study label so output files are clearly identified.
"""
import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

import yaml

from cli_ui import bold_red


def merge_fixes(fields: list, fixes: list) -> list:
    """
    Apply LLM fixup results onto the converted fields.

    Fixup responses only include the keys the LLM was asked to address, so
    this merges onto the existing field rather than replacing it outright —
    a full replacement would silently drop any key (section, title, etc.)
    the fixup prompt didn't ask about.
    """
    index = {f["name"]: i for i, f in enumerate(fields)}
    for fix in fixes:
        name = fix.get("name")
        if name and name in index:
            fields[index[name]].update(fix)
    return fields


def _constraint_str(field: dict, key: str) -> str:
    """Stringify a numeric constraint, preserving legitimate 0 values.

    `value or ""` treats 0 as falsy and blanks it out — a real bug for
    fields whose minimum is genuinely 0.
    """
    value = field.get("constraints", {}).get(key)
    return "" if value is None else str(value)


def fields_to_csv_rows(fields: list) -> list[dict]:
    rows = []
    for f in fields:
        row = {
            "schemaVersion": "0.3.2",
            "section": f.get("section", ""),
            "name": f.get("name", ""),
            "title": f.get("title", ""),
            "description": f.get("description", ""),
            "type": f.get("type", ""),
            "format": f.get("format", ""),
            "constraints.required": (
                str(f.get("constraints", {}).get("required", "")).lower()
                if f.get("constraints", {}).get("required") is not None else ""
            ),
            "constraints.maxLength": _constraint_str(f, "maxLength"),
            "constraints.enum": "|".join(f.get("constraints", {}).get("enum", [])),
            "constraints.pattern": f.get("constraints", {}).get("pattern", ""),
            "constraints.maximum": _constraint_str(f, "maximum"),
            "constraints.minimum": _constraint_str(f, "minimum"),
            "enumLabels": "|".join(
                f"{k}={v}" for k, v in (f.get("enumLabels") or {}).items()
            ),
            "enumOrdered": str(f.get("enumOrdered", "")).lower() if "enumOrdered" in f else "",
            "missingValues": "|".join(f.get("missingValues", [])),
            "trueValues": "|".join(f.get("trueValues", [])),
            "falseValues": "|".join(f.get("falseValues", [])),
            "standardsMappings[0].instrument.url": "",
            "standardsMappings[0].instrument.source": "",
            "standardsMappings[0].instrument.title": "",
            "standardsMappings[0].instrument.id": "",
            "standardsMappings[0].item.url": "",
            "standardsMappings[0].item.source": "",
            "standardsMappings[0].item.id": "",
            "relatedConcepts[0].url": (f.get("relatedConcepts") or [{}])[0].get("url", ""),
            "relatedConcepts[0].title": (f.get("relatedConcepts") or [{}])[0].get("title", ""),
            "relatedConcepts[0].source": (f.get("relatedConcepts") or [{}])[0].get("source", ""),
            "custom": "|".join(
                f"{k}={v}" for k, v in (f.get("custom") or {}).items()
            ),
        }
        rows.append(row)
    return rows


# Maps this pipeline's internal format-YAML name to the canonical inputtype
# vocabulary used by heal-transformers/scripts/convert2vlmd.py's detect_input_type().
_INPUTTYPE_MAP = {
    "stata":       "stata",
    "redcap":      "redcap-csv",
    "hbcd":        "csv-data-dict",
    "generic-csv": "csv-data-dict",
}

_GITHUB_DD_BASE = (
    "https://github.com/heal-data-stewards/heal-data-dictionaries"
    "/tree/main/data-dictionaries"
)


def write_metadata_yaml(output_dir: Path, hdp_id: str, appl_id: str,
                        stem: str, title: str, format_name: str,
                        input_filename: str | None = None):
    input_file = input_filename or f"{stem}.csv"
    inputtype = _INPUTTYPE_MAP.get(format_name, format_name)
    github_base = f"{_GITHUB_DD_BASE}/{hdp_id}" if hdp_id else None

    meta = {
        "Project": {
            "HDP_ID": hdp_id,
            "APPL_ID": appl_id,
            "Filename": stem,
            "ProjectTitle": title,
            "Status": "READY",
            "LastModified": date.today().isoformat(),
            "ProjectType": "HEAL Research Programs",
        },
        stem: {
            "inputtype": inputtype,
            "relative_input_filepath": f"../../input/{input_file}",
            "relative_output_filepath": f"./{stem}.vlmd.json",
        },
    }
    if github_base:
        meta[stem]["input_filepath"] = f"{github_base}/input/{input_file}"
        meta[stem]["output_filepath"] = f"{github_base}/vlmd/{stem}/{stem}.vlmd.json"

    yaml_path = output_dir / "metadata.yaml"
    with open(yaml_path, "w", encoding="utf-8") as fh:
        yaml.dump(meta, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)
    print(f"  metadata.yaml → {yaml_path}", flush=True)


# Typographic Unicode punctuation → plain ASCII equivalents. Applied as a
# final pass so output is portable regardless of source format (PDF text
# extraction, Word-authored REDCap/Qualtrics exports, etc. commonly carry
# smart quotes and other typographic characters).
_TEXT_NORMALIZE_MAP = {
    "‘": "'", "’": "'",   # ' '
    "“": '"', "”": '"',  # " "
    "–": "-", "—": "-",  # – —
    "…": "...",               # …
    " ": " ",                 # non-breaking space
}


def _normalize_text(value):
    if not isinstance(value, str):
        return value
    for orig, repl in _TEXT_NORMALIZE_MAP.items():
        value = value.replace(orig, repl)
    return value


def normalize_fields(fields: list) -> list:
    """Strip smart quotes / typographic punctuation from field text in place."""
    for f in fields:
        for key in ("title", "description", "section"):
            if key in f:
                f[key] = _normalize_text(f[key])
        if isinstance(f.get("enumLabels"), dict):
            f["enumLabels"] = {k: _normalize_text(v) for k, v in f["enumLabels"].items()}
    return fields


def validate_output(vlmd_doc: dict) -> bool:
    try:
        from healdata_utils import validate_vlmd_json
        result = validate_vlmd_json(vlmd_doc)
        report = result.get("report", result)
        is_valid = report.get("valid", False)
        errors = report.get("errors", [])
        print(f"  Validation: valid={is_valid}, errors={len(errors)}", flush=True)
        if errors:
            for e in errors[:5]:
                print(f"    - {e}", flush=True)
            if len(errors) > 5:
                print(f"    ... and {len(errors) - 5} more", flush=True)
        return is_valid
    except Exception as e:
        # Fail closed: a validator that can't run (e.g. healdata_utils missing
        # a transitive dependency) must not let unvalidated output through.
        print(f"  ERROR: validation could not run ({e})", flush=True)
        return False


def merge(converted_path: str, fixes_path: str | None, output_dir: str,
          hdp_id: str, appl_id: str, title: str,
          study_label: str, format_name: str, validate: bool,
          file_stem: str | None = None,
          input_filename: str | None = None) -> int:
    fields = json.loads(Path(converted_path).read_text(encoding="utf-8"))
    print(f"Loaded {len(fields):,} converted fields", flush=True)

    if fixes_path and Path(fixes_path).exists():
        fixes = json.loads(Path(fixes_path).read_text(encoding="utf-8"))
        if fixes:
            print(f"Applying {len(fixes)} LLM fixes ...", flush=True)
            fields = merge_fixes(fields, fixes)

    fields = normalize_fields(fields)

    vlmd_doc = {
        "schemaVersion": "0.3.2",
        "title": title,
        "description": (
            f"Converted from {format_name} format to HEAL VLMD schema v0.3.2. "
            f"Study ID: {hdp_id or appl_id}"
        ),
        "fields": fields,
    }

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    if file_stem:
        stem = file_stem
    else:
        label = study_label.replace(" ", "_") if study_label else "DataDictionary"
        stem = f"{appl_id}_{label}"

    # Validate before writing — gate output on schema validity
    validation_passed = True
    if validate:
        validation_passed = validate_output(vlmd_doc)

    json_path = out / f"{stem}.vlmd.json"
    json_path.write_text(
        json.dumps(vlmd_doc, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  JSON → {json_path}", flush=True)

    csv_path = out / f"{stem}.vlmd.csv"
    rows = fields_to_csv_rows(fields)
    if rows:
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"  CSV  → {csv_path}", flush=True)

    write_metadata_yaml(out, hdp_id, appl_id, stem, title, format_name, input_filename)
    print(f"\nDone. {len(fields):,} fields written.", flush=True)

    if validate and not validation_passed:
        print(
            bold_red("ERROR: Final VLMD document failed (or could not run) schema validation.",
                     stream=sys.stderr) + "\n"
            f"       Output written to {out} for inspection.\n"
            "       Fix validation errors before copying to the final repository.",
            file=sys.stderr, flush=True,
        )
        return 2
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--converted", default="work/vlmd_converted.json")
    ap.add_argument("--fixes", default=None)
    ap.add_argument("--output-dir", default="output")
    ap.add_argument("--hdp-id", default="")
    ap.add_argument("--appl-id", default="unknown")
    ap.add_argument("--title", default="HEAL Study Data Dictionary")
    ap.add_argument("--study-label", default="DataDictionary",
                    help="Short label used in output filename: {appl_id}_{study_label}.vlmd.json")
    ap.add_argument("--format-name", default="unknown", help="Source format name for metadata")
    ap.add_argument("--validate", action="store_true")
    args = ap.parse_args()
    sys.exit(merge(
        args.converted, args.fixes, args.output_dir,
        args.hdp_id, args.appl_id, args.title,
        args.study_label, args.format_name, args.validate,
    ))


if __name__ == "__main__":
    main()

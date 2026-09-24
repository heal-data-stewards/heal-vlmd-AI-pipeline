#!/usr/bin/env python3
"""
Review a VLMD conversion (input CSV codebook -> output VLMD JSON) against the
HEAL VLMD JSON schema and a set of data-quality heuristics.

Usage:
    python3 vlmd_pr_review.py --input input.csv --output output.vlmd.json \
        --schema heal_json.json [--csv output.vlmd.csv] [--csv-schema heal_csv.json]
"""
import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict

try:
    import jsonschema
except ImportError:
    jsonschema = None

ENUM_CARDINALITY_FLAG = 20  # enum with more distinct values than this -> maybe should be a range


def load_input_csv(path):
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        rows = list(csv.DictReader(f))
    return rows


def load_vlmd_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def schema_validate(doc, schema_path):
    if jsonschema is None:
        return ["jsonschema package not installed - skipped formal schema validation"]
    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)
    validator = jsonschema.Draft7Validator(schema)
    errors = []
    for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in err.path) or "<root>"
        errors.append(f"{loc}: {err.message}")
    return errors


def looks_like_number(s):
    if s is None or s == "":
        return False
    try:
        float(s)
        return True
    except ValueError:
        return False


def check_field_quality(field, findings):
    name = field.get("name", "<unnamed>")

    desc = (field.get("description") or "").strip()
    if not desc:
        findings["empty_description"].append(name)
    elif desc.lower() == name.lower():
        findings["description_equals_name"].append(name)
    elif len(desc) <= 3:
        findings["suspiciously_short_description"].append((name, desc))

    enum_labels = field.get("enumLabels") or {}
    enum_vals = (field.get("constraints") or {}).get("enum") or []

    # Enum values / labels leaking into the free-text description
    # (e.g. description already spells out "1=Yes, 2=No" that enumLabels also encodes)
    # Threshold of 3 filters out incidental 2-word matches from natural question
    # phrasing like "Remote or on-site?" (enumLabels: Remote / On-site) which are
    # not real duplication - just re-check the 2-hit bucket by hand, it's noisy.
    if enum_labels and desc:
        label_hits = [label for label in enum_labels.values()
                      if isinstance(label, str) and len(label) > 2 and label.lower() in desc.lower()]
        if len(label_hits) >= 3:
            findings["enum_values_duplicated_in_description"].append(name)
        elif len(label_hits) == 2:
            findings["enum_values_possibly_duplicated_in_description"].append(name)

    # High-cardinality enum that looks numeric -> candidate for min/max instead of enum
    if len(enum_vals) > ENUM_CARDINALITY_FLAG:
        numeric_frac = sum(looks_like_number(v) for v in enum_vals) / len(enum_vals)
        if numeric_frac > 0.9:
            findings["large_numeric_enum_should_be_range"].append((name, len(enum_vals)))
        else:
            findings["large_enum_cardinality"].append((name, len(enum_vals)))

    # type sensibility: numeric type but enum of non-numeric strings, or vice versa
    ftype = field.get("type")
    if ftype in ("number", "integer") and enum_vals:
        non_numeric = [v for v in enum_vals if not looks_like_number(v)]
        if non_numeric:
            findings["type_enum_mismatch"].append((name, ftype, non_numeric[:5]))

    # custom field duplicating a named/core property
    custom = field.get("custom") or {}
    core_keys = {"name", "title", "description", "type", "format", "section",
                 "constraints", "enumLabels", "enumOrdered", "missingValues",
                 "trueValues", "falseValues", "standardsMappings", "relatedConcepts"}
    for ck, cv in custom.items():
        if isinstance(cv, str):
            if field.get("description") and cv.strip() and cv.strip() == field["description"].strip():
                findings["custom_duplicates_description"].append((name, ck))
            if field.get("section") and cv.strip() == str(field["section"]).strip() and ck.lower() != "section":
                findings["custom_duplicates_section"].append((name, ck))
        if ck in core_keys:
            findings["custom_key_shadows_core_property"].append((name, ck))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="original input codebook CSV")
    ap.add_argument("--output", required=True, help="generated VLMD .vlmd.json")
    ap.add_argument("--schema", required=True, help="heal_json.json schema path")
    ap.add_argument("--name-column", default="name", help="column in input CSV holding the variable name")
    args = ap.parse_args()

    input_rows = load_input_csv(args.input)
    doc = load_vlmd_json(args.output)
    fields = doc.get("fields", [])

    print("=" * 70)
    print("STRUCTURAL CHECKS")
    print("=" * 70)
    print(f"Input variables (rows):  {len(input_rows)}")
    print(f"Output VLMD fields:      {len(fields)}")
    if len(input_rows) != len(fields):
        print(f"  MISMATCH: {len(input_rows) - len(fields)} variable(s) missing/added")
    else:
        print("  OK: counts match")

    input_names = [r.get(args.name_column, "").strip() for r in input_rows]
    output_names = [f.get("name", "").strip() for f in fields]
    in_set, out_set = set(input_names), set(output_names)
    missing_in_output = in_set - out_set
    extra_in_output = out_set - in_set
    if missing_in_output:
        print(f"  Names in input but NOT in output ({len(missing_in_output)}): {list(missing_in_output)[:10]}")
    if extra_in_output:
        print(f"  Names in output but NOT in input ({len(extra_in_output)}): {list(extra_in_output)[:10]}")

    dupes = [n for n, c in Counter(output_names).items() if c > 1]
    if dupes:
        print(f"  DUPLICATE names in output ({len(dupes)}): {dupes[:10]}")

    print()
    errors = schema_validate(doc, args.schema)
    print(f"JSON Schema violations: {len(errors)}")
    for e in errors[:25]:
        print(f"  - {e}")
    if len(errors) > 25:
        print(f"  ... and {len(errors) - 25} more")

    print()
    print("=" * 70)
    print("QUALITY HEURISTICS")
    print("=" * 70)
    findings = defaultdict(list)
    for f in fields:
        check_field_quality(f, findings)

    labels = {
        "empty_description": "Empty description",
        "description_equals_name": "Description is just the variable name",
        "suspiciously_short_description": "Suspiciously short description",
        "enum_values_duplicated_in_description": "Enum labels duplicated inside description text (>=3 labels echoed - high confidence)",
        "enum_values_possibly_duplicated_in_description": "Enum labels possibly echoed in description (only 2 labels matched - check for false positives from natural phrasing)",
        "large_numeric_enum_should_be_range": f"Numeric enum with >{ENUM_CARDINALITY_FLAG} values (candidate for min/max range)",
        "large_enum_cardinality": f"Non-numeric enum with >{ENUM_CARDINALITY_FLAG} values",
        "type_enum_mismatch": "type is number/integer but enum has non-numeric values",
        "custom_duplicates_description": "custom.<key> duplicates the description field",
        "custom_duplicates_section": "custom.<key> duplicates the section field",
        "custom_key_shadows_core_property": "custom.<key> reuses a name that's already a core VLMD property",
    }
    for key, label in labels.items():
        items = findings[key]
        print(f"\n{label}: {len(items)}")
        for it in items[:15]:
            print(f"  - {it}")
        if len(items) > 15:
            print(f"  ... and {len(items) - 15} more")


if __name__ == "__main__":
    main()

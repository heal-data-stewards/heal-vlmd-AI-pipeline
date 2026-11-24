"""Validate converted VLMD fields against the HEAL schema.

Generic — format-agnostic. Identical logic to hbcd_lint.py, now with
a configurable title argument so it works for any study.
"""

import argparse
import json
import sys
from pathlib import Path


def lint(fields_path: str, report_path: str, title: str = "HEAL Study Data Dictionary") -> int:
    fields = json.loads(Path(fields_path).read_text(encoding="utf-8"))
    print(f"Validating {len(fields):,} fields ...", flush=True)

    vlmd_doc = {
        "schemaVersion": "0.3.2",
        "title": title,
        "fields": fields,
    }

    try:
        from healdata_utils import validate_vlmd_json

        result = validate_vlmd_json(vlmd_doc)
    except Exception as e:  # noqa: BLE001 -- validator failure of any kind is exit 1
        print(f"ERROR: validate_vlmd_json() failed: {e}", file=sys.stderr)
        return 1

    report = result.get("report", result)
    is_valid = report.get("valid", False)
    errors = report.get("errors", [])

    Path(report_path).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"  Valid: {is_valid}", flush=True)
    print(f"  Errors: {len(errors)}", flush=True)
    if errors:
        for e in errors[:10]:
            print(f"    - {e}", flush=True)
        if len(errors) > 10:
            print(f"    ... and {len(errors) - 10} more (see {report_path})", flush=True)
    return 0 if is_valid else 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fields", default="work/vlmd_converted.json")
    ap.add_argument("--report", default="work/vlmd_validation_report.json")
    ap.add_argument("--title", default="HEAL Study Data Dictionary")
    args = ap.parse_args()
    sys.exit(lint(args.fields, args.report, args.title))


if __name__ == "__main__":
    main()

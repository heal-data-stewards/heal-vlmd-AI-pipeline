"""Save a confirmed column mapping as a reusable format YAML.

Used when vlmd_detect.py returns an unknown format. After the user confirms
(or corrects) the proposed mapping through conversation with Claude, this
script saves it as formats/{applid}.yaml so future files from the same study
are recognized automatically.

Usage:
    python vlmd_interview.py save \
        --applid 12345 \
        --mapping-json '{"name_column":"VarName",...}' \
        --hdp-id HDP01258 \
        --source-file "my_data_dictionary.csv" \
        --columns "VarName,QuestionText,DataType,..." \
        [--formats-dir formats]

    python vlmd_interview.py show \
        --detection-json work/vlmd_detection.json
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import yaml

FORMATS_DIR = Path(__file__).parent / "formats"


def build_format_yaml(
    applid: str,
    mapping: dict,
    hdp_id: str = "",
    source_file: str = "",
    columns: list[str] | None = None,
) -> dict:
    """Convert a proposed_mapping dict to a full format YAML structure."""
    today = date.today().isoformat()

    # Derive signature columns for future detection from the mapped columns
    strong_cols = [
        v
        for k, v in {
            "name_column": mapping.get("name_column"),
            "description_column": mapping.get("description_column"),
        }.items()
        if v
    ]
    supporting_cols = [
        v
        for v in [
            (mapping.get("type_mapping") or {}).get("source_column"),
            (mapping.get("levels") or {}).get("source_column"),
            (mapping.get("section") or {}).get("primary_column"),
            mapping.get("minimum_column"),
            mapping.get("maximum_column"),
            mapping.get("value_labels_column"),
        ]
        if v
    ]

    doc: dict = {
        "format_name": str(applid),
        "description": f"Custom format for APPL_ID {applid}",
        "_source": {
            "appl_id": str(applid),
            "hdp_id": hdp_id or "",
            "created": today,
            "source_file": source_file or "",
            "note": "Auto-generated from interactive column mapping session",
        },
        "input_reader": mapping.get("input_reader", "csv"),
        "name_column": mapping.get("name_column"),
        "description_column": mapping.get("description_column"),
        "title_column": mapping.get("title_column"),
    }

    # type_mapping
    tm = mapping.get("type_mapping") or {}
    if tm.get("source_column"):
        doc["type_mapping"] = {
            "source_column": tm["source_column"],
            "lookup": tm.get("lookup", {}),
        }

    # enum_ordered
    eo = mapping.get("enum_ordered") or {}
    doc["enum_ordered"] = {
        "source_column": eo.get("source_column"),
        "trigger_values": eo.get("trigger_values", []),
    }

    # section
    sec = mapping.get("section") or {}
    doc["section"] = {
        "primary_column": sec.get("primary_column"),
        "fallback_column": sec.get("fallback_column"),
    }

    # levels
    lev = mapping.get("levels") or {}
    if lev.get("source_column"):
        doc["levels"] = {
            "source_column": lev["source_column"],
            "format": lev.get("format", "auto_detect"),
            "pair_separator": lev.get("pair_separator", ","),
            "choice_separator": lev.get("choice_separator", "|"),
        }

    if mapping.get("minimum_column"):
        doc["minimum_column"] = mapping["minimum_column"]
    if mapping.get("maximum_column"):
        doc["maximum_column"] = mapping["maximum_column"]
    if mapping.get("value_labels_column"):
        doc["value_labels_column"] = mapping["value_labels_column"]

    doc["related_concepts"] = mapping.get("related_concepts", [])
    doc["custom_columns"] = mapping.get("custom_columns", [])
    doc["capture_unmapped_as_custom"] = mapping.get("capture_unmapped_as_custom", True)

    doc["fixup_prompt"] = "prompts/generic_fixup_prompt.md"
    doc["system_invariants_prompt"] = "prompts/system_invariants_vlmd.md"

    doc["detection"] = {
        "signature_columns": {
            "strong": strong_cols,
            "supporting": supporting_cols,
        },
        "confidence_threshold": 0.6,
        "file_extensions": [],
        "source_study_appl_id": str(applid),
    }

    return doc


def save_format(
    applid: str,
    mapping: dict,
    hdp_id: str = "",
    source_file: str = "",
    columns: list[str] | None = None,
    formats_dir: Path = FORMATS_DIR,
) -> str:
    doc = build_format_yaml(applid, mapping, hdp_id, source_file, columns)
    out_path = formats_dir / f"{applid}.yaml"

    if out_path.exists():
        backup = formats_dir / f"{applid}.yaml.bak"
        out_path.rename(backup)
        print(f"  Existing format backed up to {backup.name}", flush=True)

    with open(out_path, "w", encoding="utf-8") as fh:
        yaml.dump(doc, fh, default_flow_style=False, allow_unicode=True, sort_keys=False, indent=2)

    print(f"  Saved format YAML → {out_path}", flush=True)
    return str(out_path)


def show_detection(detection_path: str):
    """Pretty-print detection result for the user (called by skill)."""
    from vlmd_detect import format_detection_message

    result = json.loads(Path(detection_path).read_text(encoding="utf-8"))
    print(format_detection_message(result))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command")

    save_p = sub.add_parser("save", help="Save a confirmed mapping as {applid}.yaml")
    save_p.add_argument("--applid", required=True)
    save_p.add_argument(
        "--mapping-json", required=True, help="JSON string of the confirmed proposed_mapping dict"
    )
    save_p.add_argument("--hdp-id", default="")
    save_p.add_argument("--source-file", default="")
    save_p.add_argument(
        "--columns", default="", help="Comma-separated list of original file columns"
    )
    save_p.add_argument("--formats-dir", default=str(FORMATS_DIR))

    show_p = sub.add_parser("show", help="Display detection result")
    show_p.add_argument("--detection-json", required=True)

    args = ap.parse_args()
    if not args.command:
        ap.print_help()
        sys.exit(1)

    if args.command == "save":
        try:
            mapping = json.loads(args.mapping_json)
        except json.JSONDecodeError as e:
            print(f"ERROR: --mapping-json is not valid JSON: {e}", file=sys.stderr)
            sys.exit(1)

        cols = [c.strip() for c in args.columns.split(",")] if args.columns else None
        yaml_path = save_format(
            applid=args.applid,
            mapping=mapping,
            hdp_id=args.hdp_id,
            source_file=args.source_file,
            columns=cols,
            formats_dir=Path(args.formats_dir),
        )
        print(json.dumps({"saved_yaml": yaml_path}))

    elif args.command == "show":
        show_detection(args.detection_json)


if __name__ == "__main__":
    main()

"""Generic YAML-driven VLMD converter.

Reads a format YAML spec and applies all mapping rules to convert any
supported data dictionary format to VLMD fields JSON.

Replaces hbcd_convert.py — run with --format formats/hbcd.yaml to get
identical output.
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import ftfy
import pandas as pd
import yaml

from cli_ui import bold_yellow


def _detect_encoding(file_path: str) -> str:
    # Must check the whole file, not just a sample — a non-UTF-8 byte
    # anywhere (e.g. after a manual edit in Excel/TextEdit) will otherwise
    # be missed here and crash pandas partway through the real read.
    try:
        with open(file_path, encoding="utf-8") as f:
            f.read()
        return "utf-8"
    except UnicodeDecodeError:
        return "latin-1"


# ── Label cleaning strategies ────────────────────────────────────────────────


def _strip_outer_quotes(s: str) -> str:
    """Remove one layer of outer escaped double-quotes from a label string."""
    s = s.strip()
    if s.startswith('"') and s.endswith('"') and len(s) > 2:
        inner = s[1:-1]
        if not (inner.startswith('"') and inner.endswith('"')):
            return inner
    return s


LABEL_CLEANERS = {
    "strip_outer_quotes": _strip_outer_quotes,
    "strip": str.strip,
    None: str.strip,
}


# ── Levels parsers ───────────────────────────────────────────────────────────


def _parse_json_array(
    raw: str, value_key: str, label_key: str, cleaner
) -> tuple[list[str], dict[str, str]]:
    if not raw or raw.strip() in ("", "[]", "nan"):
        return [], {}
    try:
        items = json.loads(raw.strip())
    except (json.JSONDecodeError, ValueError):
        return [], {}
    if not isinstance(items, list):
        return [], {}
    enum_values = [str(item[value_key]) for item in items if value_key in item]
    enum_labels = {
        str(item[value_key]): cleaner(str(item.get(label_key, "")))
        for item in items
        if value_key in item
    }
    return enum_values, enum_labels


def _strip_wrapping_quotes(s: str) -> str:
    """Strip one matching pair of leading/trailing straight quotes.

    Some source exports wrap each key/value token in quotes, e.g.
    `'baseline_arm_1'='Baseline'` — only strips when both ends match the
    same quote character, so genuine apostrophes ("Associate's") are
    left untouched.
    """
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1].strip()
    return s


def _parse_pipe_separated(
    raw: str, pair_sep: str, choice_sep: str
) -> tuple[list[str], dict[str, str]]:
    if not raw or raw.strip() in ("", "nan"):
        return [], {}
    enum_values = []
    enum_labels = {}
    for choice in raw.split(choice_sep):
        parts = choice.split(pair_sep, 1)
        if len(parts) == 2:
            val = _strip_wrapping_quotes(parts[0].strip())
            lbl = _strip_wrapping_quotes(parts[1].strip())
            enum_values.append(val)
            enum_labels[val] = lbl
        elif len(parts) == 1 and parts[0].strip():
            val = _strip_wrapping_quotes(parts[0].strip())
            enum_values.append(val)
            enum_labels[val] = val
    return enum_values, enum_labels


def _parse_comma_separated(raw: str) -> tuple[list[str], dict[str, str]]:
    if not raw or raw.strip() in ("", "nan"):
        return [], {}
    vals = [v.strip() for v in raw.split(",") if v.strip()]
    return vals, {v: v for v in vals}


def _parse_key_value_lines(raw: str) -> dict[str, str]:
    """Parse 'VALUE=label' pairs, one per line (or pipe-separated), into a dict."""
    if not raw or raw.strip() in ("", "nan"):
        return {}
    labels: dict[str, str] = {}
    for line in raw.replace("|", "\n").splitlines():
        if "=" not in line:
            continue
        val, lbl = line.split("=", 1)
        val, lbl = val.strip(), lbl.strip()
        if val and lbl:
            labels[val] = lbl
    return labels


def _to_number(raw: str) -> Any:
    try:
        num = float(raw)
    except ValueError:
        return raw
    return int(num) if num.is_integer() else num


def parse_levels(raw: str, levels_spec: dict | None) -> tuple[list[str], dict[str, str]]:
    if not levels_spec or not raw or str(raw).strip().lower() in ("", "nan"):
        return [], {}
    fmt = levels_spec.get("format", "auto_detect")
    cleaner = LABEL_CLEANERS.get(levels_spec.get("label_cleaning"), str.strip)

    if fmt == "json_array":
        return _parse_json_array(
            raw,
            levels_spec.get("value_key", "value"),
            levels_spec.get("label_key", "label"),
            cleaner,
        )
    if fmt == "pipe_separated":
        return _parse_pipe_separated(
            raw,
            levels_spec.get("pair_separator", ","),
            levels_spec.get("choice_separator", "|"),
        )
    if fmt == "comma_separated":
        return _parse_comma_separated(raw)
    if fmt == "auto_detect":
        # Try json_array first, then pipe_separated
        result = _parse_json_array(raw, "value", "label", str.strip)
        if result[0]:
            return result
        result = _parse_pipe_separated(raw, ",", "|")
        if result[0]:
            return result
        return _parse_comma_separated(raw)
    return [], {}


# ── Column resolution helpers ────────────────────────────────────────────────


def resolve_column_spec(spec: Any, df_columns: set[str]) -> str | dict | None:
    """Resolve a column spec to an actual column name (or combine descriptor).

    - str            -> exact column name, if present
    - list           -> first candidate present in df_columns (fallback order)
    - {combine: [...], separator: ...} -> descriptor joining multiple columns
    """
    if spec is None:
        return None
    if isinstance(spec, str):
        return spec if spec in df_columns else None
    if isinstance(spec, list):
        for candidate in spec:
            if candidate and candidate in df_columns:
                return candidate
        return None
    if isinstance(spec, dict) and "combine" in spec:
        cols = [c for c in spec["combine"] if c and c in df_columns]
        if not cols:
            return None
        return {"combine": cols, "separator": spec.get("separator", " | ")}
    return None


def _clean_scalar(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    return "" if s.lower() == "nan" else s


def _normalize_for_dedup(s: str) -> str:
    return re.sub(r"[\s.,;:]+$", "", s.strip().lower())


def _dedupe_parts(parts: list[str]) -> list[str]:
    """Drop a part if it duplicates, or is a truncation of, one already kept.

    Source columns meant to be combined (e.g. "Definition" + "Short
    Description") are frequently identical or near-identical per row —
    combining them verbatim would produce a repeated/redundant description.
    """
    kept: list[str] = []
    kept_norm: list[str] = []
    for p in parts:
        norm = _normalize_for_dedup(p)
        if any(norm == k or norm in k or k in norm for k in kept_norm):
            continue
        kept.append(p)
        kept_norm.append(norm)
    return kept


def get_val(row: dict, col: str | dict | None) -> str:
    if not col:
        return ""
    if isinstance(col, dict) and "combine" in col:
        parts = [_clean_scalar(row.get(c, "")) for c in col["combine"]]
        parts = _dedupe_parts([p for p in parts if p])
        return col.get("separator", " | ").join(parts)
    return _clean_scalar(row.get(col, ""))


# ── Stata reader ──────────────────────────────────────────────────────────────


def read_stata(file_path: str) -> pd.DataFrame:
    try:
        import pyreadstat
    except ImportError:
        raise ImportError("pyreadstat required for Stata files: pip install pyreadstat")
    df, meta = pyreadstat.read_dta(file_path)
    # Build normalized dataframe with expected column names
    records = []
    for col in df.columns:
        rec = {
            "varname": col,
            "varlabel": meta.column_labels.get(col, "") or "",
            "vartype": str(df[col].dtype),
        }
        # Add value labels if present
        vl = (meta.variable_value_labels or {}).get(col, {})
        if vl:
            # Store as JSON array in a value_labels column
            items = [{"value": str(k), "label": str(v)} for k, v in vl.items()]
            rec["value_labels"] = json.dumps(items)
        else:
            rec["value_labels"] = ""
        records.append(rec)
    return pd.DataFrame(records, dtype=str)


# ── Row converter ─────────────────────────────────────────────────────────────


def row_to_vlmd_field(row: dict, spec: dict, resolved_cols: dict) -> dict:
    """Convert one row dict to a VLMD field dict using pre-resolved column names."""
    field: dict = {}

    # name (required)
    field["name"] = get_val(row, resolved_cols.get("name_column"))

    # description (required)
    desc = get_val(row, resolved_cols.get("description_column"))
    field["description"] = desc  # empty string flagged by linter

    # title
    title = get_val(row, resolved_cols.get("title_column"))
    if title:
        field["title"] = title

    # type
    type_col = resolved_cols.get("type_mapping_column")
    raw_type = ""
    if type_col:
        raw_type = get_val(row, type_col).lower()
        lookup = spec.get("type_mapping", {}).get("lookup", {})
        vlmd_type = lookup.get(raw_type)
        if vlmd_type:
            field["type"] = vlmd_type

    # type_override: when the primary type is a generic fallback (e.g. "text"→string),
    # use a secondary column (e.g. Text Validation Type) for a more specific VLMD type.
    override_spec = spec.get("type_override")
    if override_spec and raw_type == override_spec.get("when_type_equals", "").lower():
        override_col = resolved_cols.get("type_override_column")
        if override_col:
            override_raw = get_val(row, override_col).lower()
            override_type = override_spec.get("lookup", {}).get(override_raw)
            if override_type:
                field["type"] = override_type

    # boolean_values: for boolean fields with known true/false strings, emit
    # constraints.enum + enumLabels (same pattern as other enumerated fields).
    bv_spec = spec.get("boolean_values")
    if bv_spec and field.get("type") == "boolean":
        bv_col = resolved_cols.get("boolean_values_column")
        if bv_col:
            src_type = get_val(row, bv_col).lower()
            bv_mapping = {k.lower(): v for k, v in bv_spec.get("mapping", {}).items()}
            bv = bv_mapping.get(src_type)
            if bv:
                true_vals = bv.get("trueValues", [])
                false_vals = bv.get("falseValues", [])
                enum_vals = true_vals + false_vals
                if enum_vals:
                    field.setdefault("constraints", {})["enum"] = enum_vals
                    field["enumLabels"] = {v: v for v in enum_vals}

    # enumOrdered
    ordered_col = resolved_cols.get("enum_ordered_column")
    if ordered_col:
        trigger_vals = spec.get("enum_ordered", {}).get("trigger_values", [])
        if get_val(row, ordered_col).lower() in [v.lower() for v in trigger_vals]:
            field["enumOrdered"] = True

    # section
    section_val = get_val(row, resolved_cols.get("section_primary_column"))
    if not section_val:
        section_val = get_val(row, resolved_cols.get("section_fallback_column"))
    if section_val:
        field["section"] = section_val

    # levels → constraints.enum + enumLabels
    levels_col = resolved_cols.get("levels_column")
    if levels_col:
        raw_levels = get_val(row, levels_col)
        # For stata, levels come from value_labels column
        levels_source = raw_levels
        if spec.get("levels", {}).get("format") == "stata_value_labels":
            levels_source = get_val(row, "value_labels")
            levels_spec_override = {
                "format": "json_array",
                "value_key": "value",
                "label_key": "label",
            }
            enum_values, enum_labels = parse_levels(levels_source, levels_spec_override)
        else:
            enum_values, enum_labels = parse_levels(levels_source, spec.get("levels"))
        if enum_values:
            field.setdefault("constraints", {})["enum"] = enum_values
            field["enumLabels"] = enum_labels

    # constraints.minimum / constraints.maximum
    min_col = resolved_cols.get("minimum_column")
    if min_col:
        v = get_val(row, min_col)
        if v:
            field.setdefault("constraints", {})["minimum"] = _to_number(v)
    max_col = resolved_cols.get("maximum_column")
    if max_col:
        v = get_val(row, max_col)
        if v:
            field.setdefault("constraints", {})["maximum"] = _to_number(v)

    # value_labels_column → enumLabels (overrides identity labels from levels)
    value_labels_col = resolved_cols.get("value_labels_column")
    if value_labels_col:
        raw_labels = get_val(row, value_labels_col)
        parsed_labels = _parse_key_value_lines(raw_labels)
        if parsed_labels:
            field["enumLabels"] = {**field.get("enumLabels", {}), **parsed_labels}
            enum_vals = field.get("constraints", {}).get("enum", [])
            for val in parsed_labels:
                if val not in enum_vals:
                    enum_vals.append(val)
            if enum_vals:
                field.setdefault("constraints", {})["enum"] = enum_vals

    # If all enum values are integers and type is still string, upgrade to integer.
    if field.get("type") == "string":
        enum_vals = (field.get("constraints") or {}).get("enum", [])
        if enum_vals:
            try:
                [int(v) for v in enum_vals]
                field["type"] = "integer"
            except (ValueError, TypeError):
                pass

    # Drop enumLabels when every label is identical to its key — redundant with constraints.enum.
    if field.get("enumLabels") and all(k == v for k, v in field["enumLabels"].items()):
        del field["enumLabels"]

    # relatedConcepts
    rel_concepts = []
    for rc_spec in spec.get("related_concepts", []):
        url_col = rc_spec.get("url_column")
        if not url_col:
            continue
        url = get_val(row, url_col)
        if rc_spec.get("require_http") and not url.startswith("http"):
            continue
        if url:
            title_col = rc_spec.get("title_column")
            rc = {
                "url": url,
                "source": rc_spec.get("source_name", ""),
            }
            if title_col:
                t = get_val(row, title_col)
                if t:
                    rc["title"] = t
            rel_concepts.append(rc)
    if rel_concepts:
        field["relatedConcepts"] = rel_concepts

    # custom
    custom: dict = {}
    explicit_custom = spec.get("custom_columns", [])
    for col in explicit_custom:
        if col not in row:
            continue
        val = get_val(row, col)
        if val:
            custom[col] = val

    # capture_unmapped_as_custom: send everything else to custom
    if spec.get("capture_unmapped_as_custom"):
        accounted = _accounted_columns(spec, resolved_cols)
        for col, val in row.items():
            if col in accounted:
                continue
            v = get_val(row, col)
            if v:
                custom[col] = v

    if custom:
        field["custom"] = custom

    return field


# ── Encoding cleanup (deterministic, no LLM) ─────────────────────────────────
# Mojibake and smart-quote/dash artifacts are a mechanical encoding problem, not
# a judgment call — ftfy fixes them the same way every time, so this runs for
# every field automatically. The one thing it can't fix is an actual replacement
# character (U+FFFD): that means bytes were already lost before we ever saw the
# file, so it's flagged for a human/LLM to track down the original value instead.

ENCODING_FIX_KEYS = ("description", "title")


def clean_field_encoding(field: dict) -> list[dict]:
    """Fix mojibake/smart-quote artifacts in a field's text values in place.

    Returns a list of fix records — {"field", "key", "before", "after",
    "unrecoverable"} — for anything changed, plus any leftover replacement
    characters ftfy couldn't repair (unrecoverable=True, before==after).
    """
    fixes = []
    name = field.get("name", "")

    def _check(key: str, val: str):
        if not val:
            return
        fixed = ftfy.fix_text(val)
        if fixed != val:
            fixes.append(
                {
                    "field": name,
                    "key": key,
                    "before": val,
                    "after": fixed,
                    "unrecoverable": "�" in fixed,
                }
            )
            return fixed
        if "�" in val:
            fixes.append(
                {
                    "field": name,
                    "key": key,
                    "before": val,
                    "after": val,
                    "unrecoverable": True,
                }
            )
        return val

    for key in ENCODING_FIX_KEYS:
        val = field.get(key)
        if val:
            field[key] = _check(key, val)

    enum_labels = field.get("enumLabels")
    if enum_labels:
        for k, v in list(enum_labels.items()):
            if v:
                enum_labels[k] = _check(f"enumLabels.{k}", v)

    return fixes


VALID_VLMD_TYPES = {"number", "integer", "string", "boolean", "date", "datetime", "time"}

PLACEHOLDER_DESCRIPTIONS = {
    "n/a",
    "na",
    "tbd",
    "see codebook",
    "see codebook.",
    "see notes",
    "see note",
    "no description",
    "none",
    "missing",
    "unknown",
    "placeholder",
    "todo",
    "fill in",
    "to be determined",
    "not available",
    "not applicable",
}

# Descriptions at or below this word count are flagged even if they don't match
# a known placeholder — real source data has truncated/fragment text (e.g. "not")
# that isn't a recognized placeholder string but is still too short to be useful.
DESCRIPTION_REVIEW_MAX_WORDS = 1


def flag_field(field: dict) -> list[str]:
    issues = []

    desc = field.get("description", "").strip()
    if not desc:
        issues.append("missing_description")
    else:
        normalized = desc.lower().rstrip(".").strip()
        is_known_placeholder = normalized in PLACEHOLDER_DESCRIPTIONS
        is_suspiciously_short = len(desc.split()) <= DESCRIPTION_REVIEW_MAX_WORDS
        if is_known_placeholder or is_suspiciously_short:
            issues.append("description_too_short")

    if "�" in desc or "�" in field.get("title", ""):
        issues.append("encoding_corruption")

    ftype = field.get("type", "")
    if not ftype:
        issues.append("missing_type")
    elif ftype not in VALID_VLMD_TYPES:
        issues.append("type_not_in_schema")

    return issues


# ── Column resolution (pre-compute once per file) ────────────────────────────


def resolve_all_columns(spec: dict, df_columns: set[str]) -> dict:
    """Resolve all column specs from the format YAML to actual column names."""
    r: dict[str, str | None] = {}
    r["name_column"] = resolve_column_spec(spec.get("name_column"), df_columns)
    r["description_column"] = resolve_column_spec(spec.get("description_column"), df_columns)
    r["title_column"] = resolve_column_spec(spec.get("title_column"), df_columns)
    r["type_mapping_column"] = resolve_column_spec(
        spec.get("type_mapping", {}).get("source_column"), df_columns
    )
    r["type_override_column"] = resolve_column_spec(
        (spec.get("type_override") or {}).get("source_column"), df_columns
    )
    r["boolean_values_column"] = resolve_column_spec(
        (spec.get("boolean_values") or {}).get("source_column"), df_columns
    )
    r["enum_ordered_column"] = resolve_column_spec(
        (spec.get("enum_ordered") or {}).get("source_column"), df_columns
    )
    r["section_primary_column"] = resolve_column_spec(
        (spec.get("section") or {}).get("primary_column"), df_columns
    )
    r["section_fallback_column"] = resolve_column_spec(
        (spec.get("section") or {}).get("fallback_column"), df_columns
    )
    r["levels_column"] = resolve_column_spec(
        (spec.get("levels") or {}).get("source_column"), df_columns
    )
    r["minimum_column"] = resolve_column_spec(spec.get("minimum_column"), df_columns)
    r["maximum_column"] = resolve_column_spec(spec.get("maximum_column"), df_columns)
    r["value_labels_column"] = resolve_column_spec(spec.get("value_labels_column"), df_columns)
    return r


def _accounted_columns(spec: dict, resolved: dict) -> set[str]:
    """All source columns that are mapped to a VLMD field or custom_columns."""
    accounted: set[str] = set(spec.get("custom_columns", []))
    for v in resolved.values():
        if isinstance(v, dict) and "combine" in v:
            accounted.update(v["combine"])
        elif v:
            accounted.add(v)
    for rc_spec in spec.get("related_concepts") or []:
        for key in ("url_column", "title_column"):
            col = rc_spec.get(key)
            if col:
                accounted.add(col)
    return accounted


def check_unaccounted_columns(spec: dict, resolved: dict, df_columns: set[str]) -> None:
    """Warn about input columns that are neither mapped/custom nor documented
    in excluded_columns — every source column's fate should be traceable."""
    excluded = spec.get("excluded_columns") or []
    excluded_names = {e.get("column") if isinstance(e, dict) else e for e in excluded}
    excluded_names.discard(None)

    stale = sorted(excluded_names - df_columns)
    if stale:
        print(
            f"  NOTE: excluded_columns lists column(s) not present in this input: {stale}",
            flush=True,
        )

    if spec.get("capture_unmapped_as_custom"):
        return  # every unmapped column is captured automatically — nothing silently dropped

    accounted = _accounted_columns(spec, resolved) | excluded_names
    unaccounted = sorted(df_columns - accounted)
    if unaccounted:
        warning = bold_yellow(
            f"WARNING: {len(unaccounted)} input column(s) are not mapped, not in "
            f"custom_columns, and not documented in excluded_columns:",
            stream=sys.stderr,
        )
        print(f"  {warning} {unaccounted}", file=sys.stderr, flush=True)
        print(
            "    Add each to custom_columns (to keep it) or excluded_columns "
            "(with a reason, to document why it's dropped).",
            file=sys.stderr,
            flush=True,
        )


# ── Main convert function ─────────────────────────────────────────────────────


def convert(input_path: str, format_yaml: str, output_fields: str, output_lint: str) -> int:
    spec = yaml.safe_load(Path(format_yaml).read_text(encoding="utf-8"))
    format_name = spec.get("format_name", Path(format_yaml).stem)
    print(f"Format: {format_name}", flush=True)

    # Read input
    reader = spec.get("input_reader", "csv")
    if reader == "stata":
        df = read_stata(input_path)
    else:
        encoding = _detect_encoding(input_path)
        df = pd.read_csv(
            input_path, dtype=str, keep_default_na=False, low_memory=False, encoding=encoding
        )

    print(f"Reading {input_path} ...", flush=True)
    print(f"  {len(df):,} rows, {len(df.columns)} columns", flush=True)

    # Pre-resolve column names
    df_cols = set(df.columns)
    resolved = resolve_all_columns(spec, df_cols)

    if not resolved.get("name_column"):
        print(f"ERROR: could not find a name column in {list(df.columns)}", file=sys.stderr)
        return 1

    print(
        f"  Column mapping: name={resolved['name_column']!r}, "
        f"description={resolved['description_column']!r}, "
        f"type={resolved['type_mapping_column']!r}, "
        f"section={resolved['section_primary_column']!r}",
        flush=True,
    )

    check_unaccounted_columns(spec, resolved, df_cols)

    fields = []
    lint_records = []
    encoding_fixes = []

    for _, row in df.iterrows():
        row_dict = row.to_dict()
        field = row_to_vlmd_field(row_dict, spec, resolved)
        encoding_fixes.extend(clean_field_encoding(field))
        issues = flag_field(field)
        fields.append(field)
        if issues:
            lint_records.append(
                {
                    "name": field["name"],
                    "issues": issues,
                    "source_row": row_dict,
                    "vlmd_field_draft": field,
                }
            )

    Path(output_fields).write_text(
        json.dumps(fields, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    Path(output_lint).write_text(
        json.dumps(lint_records, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    if encoding_fixes:
        repaired = [f for f in encoding_fixes if not f["unrecoverable"]]
        unrecoverable = [f for f in encoding_fixes if f["unrecoverable"]]
        encoding_fixes_path = Path(output_lint).parent / "vlmd_encoding_fixes.json"
        encoding_fixes_path.write_text(
            json.dumps(encoding_fixes, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(
            f"  Encoding fixes: {len(repaired)} field value(s) auto-corrected "
            f"(mojibake/smart quotes — no LLM used); {len(unrecoverable)} have "
            f"unrecoverable corruption flagged for review. See {encoding_fixes_path}",
            flush=True,
        )

    names = [f["name"] for f in fields]
    dupes = len(names) - len(set(names))
    no_name = sum(1 for n in names if not n)

    print(f"  Converted: {len(fields):,} fields", flush=True)
    print(f"  Lint flags: {len(lint_records)}", flush=True)
    if dupes:
        print(f"  WARNING: {dupes} duplicate names", flush=True)

    if no_name:
        print(f"ERROR: {no_name} fields have no name", file=sys.stderr)
        return 1
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--format", required=True, help="Path to format YAML")
    ap.add_argument("--output-fields", default="work/vlmd_converted.json")
    ap.add_argument("--output-lint", default="work/vlmd_lint_report.json")
    args = ap.parse_args()
    sys.exit(convert(args.input, args.format, args.output_fields, args.output_lint))


if __name__ == "__main__":
    main()

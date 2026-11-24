"""Shared helpers for the data-dictionary test suite: manifest loading, running a
fixture through the offline stages (extract -> detect -> convert -> lint), and
checking the result against a case's `expect` block.

Expectations are properties (format, counts, flagged fields, preserved labels), not
golden output, so tests don't break when descriptions or field order change.
"""

import json
import re
from pathlib import Path

import yaml

SUITE_DIR = Path(__file__).parent
MANIFEST = SUITE_DIR / "manifest.yaml"
FAILURE_MODES = SUITE_DIR / "failure_modes.yaml"
SOURCES_LOCAL = SUITE_DIR / "sources.local.yaml"

HTML_TAG = re.compile(r"</?[a-zA-Z][^>]*>|&nbsp;|&amp;|&lt;|&gt;")


def load_manifest() -> list[dict]:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8")) or []


def load_failure_modes() -> dict[str, str]:
    groups = yaml.safe_load(FAILURE_MODES.read_text(encoding="utf-8"))
    return {fid: desc for group in groups.values() for fid, desc in group.items()}


def fixture_path(case: dict) -> Path:
    return SUITE_DIR / "fixtures" / case["id"] / case["fixture"]


def run_offline(case: dict, work: Path) -> dict:
    """Run one fixture through extract -> detect -> convert -> lint without any LLM call.

    Returns a dict with whatever each stage produced; stages after a failed or
    rejecting stage are absent.
    """
    import vlmd_convert
    import vlmd_detect
    import vlmd_excel
    import vlmd_lint

    work.mkdir(parents=True, exist_ok=True)
    src = fixture_path(case)
    run: dict = {"input": str(src)}

    if src.suffix.lower() in (".xlsx", ".xls"):
        extracted = work / "extracted.csv"
        run["extract_rc"] = vlmd_excel.extract(str(src), str(extracted), case.get("sheet"))
        if run["extract_rc"] != 0:
            return run
        src = extracted

    det = vlmd_detect.detect(str(src), llm_fallback=False)
    run["detect"] = det
    if not det.get("format_name") or not det.get("format_yaml_path"):
        return run

    fields_path, lint_path = work / "fields.json", work / "lint.json"
    run["convert_rc"] = vlmd_convert.convert(
        str(src), det["format_yaml_path"], str(fields_path), str(lint_path)
    )
    if run["convert_rc"] != 0:
        return run
    run["fields"] = json.loads(fields_path.read_text(encoding="utf-8"))
    run["lint"] = json.loads(lint_path.read_text(encoding="utf-8"))
    run["lint_rc"] = vlmd_lint.lint(str(fields_path), str(work / "validation.json"), case["id"])
    return run


def _as_list(v) -> list:
    return v if isinstance(v, list) else [v]


def check_field(name: str, want: dict, field: dict | None) -> list[str]:
    if field is None:
        return [f"field {name!r}: missing from output"]
    errs = []
    blob = json.dumps(field, ensure_ascii=False)
    enum = (field.get("constraints") or {}).get("enum")
    if "type" in want and field.get("type") not in _as_list(want["type"]):
        errs.append(f"field {name!r}: type {field.get('type')!r}, expected one of {want['type']}")
    for key in ("minimum", "maximum"):
        if key in want and (field.get("constraints") or {}).get(key) != want[key]:
            got = (field.get("constraints") or {}).get(key)
            errs.append(f"field {name!r}: constraints.{key} is {got!r}, expected {want[key]!r}")
    if want.get("no_enum") and enum:
        errs.append(f"field {name!r}: has constraints.enum {enum!r}, expected none")
    for text in want.get("not_in_enum", []):
        if enum and any(text in str(v) for v in enum):
            errs.append(f"field {name!r}: label text {text!r} ended up in constraints.enum")
    for text in want.get("labels_preserved", []):
        if text not in blob:
            errs.append(f"field {name!r}: label {text!r} not found anywhere in the field")
    if want.get("no_html") and HTML_TAG.search(field.get("description") or ""):
        errs.append(f"field {name!r}: HTML left in description")
    if want.get("description_nonempty") and not (field.get("description") or "").strip():
        errs.append(f"field {name!r}: empty description")
    return errs


def check_ids(case: dict) -> list[str]:
    """One id per assertion in case['expect'], so each can pass, fail or xfail on its own."""
    want = case.get("expect", {})
    ids = [k for k in ("format", "rows_in", "fields_out", "schema_valid") if k in want]
    ids += [f"flagged:{n}" for n in want.get("flagged", [])]
    ids += [f"not_flagged:{n}" for n in want.get("not_flagged", [])]
    ids += [f"field:{n}" for n in (want.get("fields") or {})]
    return ids


def _expected_count(want_value, got) -> bool:
    return got in want_value if isinstance(want_value, list) else got == want_value


def check_one(case: dict, run: dict, check_id: str) -> list[str]:
    """Evaluate one assertion from check_ids(); return a list of human-readable failures."""
    want = case["expect"]
    det = run.get("detect") or {}
    kind, _, name = check_id.partition(":")

    if kind == "format":
        got = det.get("format_name")
        return [] if got == want["format"] else [f"detected {got!r}, expected {want['format']!r}"]
    if kind == "rows_in":
        got = det.get("row_count")
        return (
            []
            if _expected_count(want["rows_in"], got)
            else [f"saw {got} rows, expected {want['rows_in']}"]
        )

    fields = run.get("fields")
    if fields is None:
        return [f"no fields produced (convert_rc={run.get('convert_rc')})"]
    if kind == "fields_out":
        got = len(fields)
        return (
            []
            if _expected_count(want["fields_out"], got)
            else [f"{got} fields out, expected {want['fields_out']}"]
        )
    if kind == "schema_valid":
        ok = run.get("lint_rc") == 0
        return [] if ok == want["schema_valid"] else [f"validation rc={run.get('lint_rc')}"]

    flagged = {r["name"] for r in run.get("lint", [])}
    if kind == "flagged":
        return [] if name in flagged else [f"{name!r} was not flagged for review"]
    if kind == "not_flagged":
        return [f"{name!r} was flagged for review"] if name in flagged else []
    if kind == "field":
        by_name = {f["name"]: f for f in fields}
        return check_field(name, want["fields"][name], by_name.get(name))
    raise ValueError(f"unknown check id {check_id!r}")

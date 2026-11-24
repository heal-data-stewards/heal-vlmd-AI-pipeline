"""Cut a small, targeted excerpt out of a local data dictionary for use as a fixture.

Whole dictionaries are never committed. An excerpt keeps the rows that show the problem
(for example every slider row, plus a few ordinary ones) and the header. Where each
fixture came from is recorded in sources.local.yaml, which is gitignored: it holds your
local paths, the source hash, the sheet and the row selection.

    python tests/suite/build_fixtures.py extract --case redcap-slider-minimal \\
        --source ~/dds/Project_DataDictionary.csv --first 3 \\
        --match 'Field Type=^slider$'
    python tests/suite/build_fixtures.py extract --case multi-sheet-ndar \\
        --source ~/dds/ndar.xlsx --sheet ndar_subject01_def --sheet phq01_definitions --first 12
    python tests/suite/build_fixtures.py extract --case pdf-qualtrics \\
        --source ~/dds/codebook.pdf --pages 3-5
    python tests/suite/build_fixtures.py list
    python tests/suite/build_fixtures.py rebuild [--case ID]

Row selection (all optional, combined, kept in source order; `--first 0` selects nothing):
    --first N         the first N data rows (default 10)
    --rows 3,7,10-12  1-based data-row numbers (the row after the header is 1)
    --match COL=RE    rows where column COL (or `*` for any) matches regex RE;
                      at most --max-match (default 10) rows per --match
    --header-row N    1-based header row, for sheets with title rows above it (default 1);
                      the rows above the header are copied as-is
"""

import argparse
import csv
import datetime
import hashlib
import io
import re
import sys
from pathlib import Path

import suite_lib
import yaml


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_rows(spec: str) -> set[int]:
    out: set[int] = set()
    for part in filter(None, (p.strip() for p in spec.split(","))):
        lo, _, hi = part.partition("-")
        out.update(range(int(lo), int(hi or lo) + 1))
    return out


def select_rows(rows: list[list[str]], args: dict) -> list[list[str]]:
    """Return preamble + header + the selected data rows of one table (list of string rows)."""
    hdr_i = args.get("header_row", 1) - 1
    if hdr_i >= len(rows):
        return rows
    header, body = rows[hdr_i], rows[hdr_i + 1 :]
    keep: set[int] = set(range(1, args.get("first", 10) + 1))
    keep |= _parse_rows(args.get("rows", ""))
    for m in args.get("match", []):
        col, _, pattern = m.partition("=")
        rx = re.compile(pattern)
        idx = None if col == "*" else header.index(col)
        hits = [
            n
            for n, r in enumerate(body, 1)
            if (any(rx.search(c) for c in r) if idx is None else idx < len(r) and rx.search(r[idx]))
        ]
        keep |= set(hits[: args.get("max_match", 10)])
    return rows[: hdr_i + 1] + [r for n, r in enumerate(body, 1) if n in keep]


def _read_text(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
        return text, "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"


def extract_delimited(src: Path, dest: Path, args: dict) -> None:
    text, enc = _read_text(src)
    text = text.lstrip("﻿")
    delim = "\t" if src.suffix.lower() == ".tab" else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    buf = io.StringIO()
    csv.writer(buf, delimiter=delim, lineterminator="\n").writerows(select_rows(rows, args))
    dest.write_bytes(buf.getvalue().encode(enc))


def extract_xlsx(src: Path, dest: Path, args: dict) -> None:
    import openpyxl

    wb = openpyxl.load_workbook(src, data_only=True)
    out = openpyxl.Workbook()
    out.remove(out.active)
    wanted = args.get("sheets") or wb.sheetnames
    for name in wanted:
        ws = wb[name]
        rows = [["" if c is None else str(c) for c in r] for r in ws.iter_rows(values_only=True)]
        new = out.create_sheet(name)
        new.sheet_state = ws.sheet_state
        for r in select_rows(rows, args):
            new.append(r)
    out.save(dest)


def extract_pdf(src: Path, dest: Path, args: dict) -> None:
    from pypdf import PdfReader, PdfWriter

    reader, writer = PdfReader(str(src)), PdfWriter()
    for n in sorted(_parse_rows(args["pages"])):
        writer.add_page(reader.pages[n - 1])
    with dest.open("wb") as f:
        writer.write(f)


def build(case_id: str, source: Path, args: dict) -> Path:
    suffix = source.suffix.lower()
    dest_dir = suite_lib.SUITE_DIR / "fixtures" / case_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / ("input" + suffix)
    if suffix in (".csv", ".tab"):
        extract_delimited(source, dest, args)
    elif suffix == ".xlsx":
        extract_xlsx(source, dest, args)
    elif suffix == ".pdf":
        extract_pdf(source, dest, args)
    else:
        sys.exit(f"don't know how to excerpt a {suffix} file")
    return dest


def _load_sources() -> dict:
    if suite_lib.SOURCES_LOCAL.exists():
        return yaml.safe_load(suite_lib.SOURCES_LOCAL.read_text(encoding="utf-8")) or {}
    return {}


def _save_sources(sources: dict) -> None:
    suite_lib.SOURCES_LOCAL.write_text(
        "# Local only (gitignored): where each fixture was extracted from.\n"
        + yaml.safe_dump(sources, sort_keys=True, allow_unicode=True),
        encoding="utf-8",
    )


def _record(case_id: str, source: Path, args: dict, dest: Path) -> None:
    sources = _load_sources()
    sources[case_id] = {
        "source": str(source),
        "sha256": _sha256(source),
        "args": args,
        "fixture": str(dest.relative_to(suite_lib.SUITE_DIR)),
        "built": datetime.date.today().isoformat(),
    }
    _save_sources(sources)


def cmd_extract(ns) -> int:
    source = Path(ns.source).expanduser().resolve()
    if not source.is_file():
        sys.exit(f"no such file: {source}")
    args = {"first": ns.first, "max_match": ns.max_match, "header_row": ns.header_row}
    for key in ("rows", "pages"):
        if getattr(ns, key):
            args[key] = getattr(ns, key)
    if ns.match:
        args["match"] = ns.match
    if ns.sheet:
        args["sheets"] = ns.sheet
    dest = build(ns.case, source, args)
    _record(ns.case, source, args, dest)
    print(f"wrote {dest.relative_to(suite_lib.SUITE_DIR.parent.parent)}")
    print("recorded the source in tests/suite/sources.local.yaml (gitignored)")
    return 0


def cmd_list(_ns) -> int:
    for case_id, rec in _load_sources().items():
        exists = Path(rec["source"]).is_file()
        print(f"{case_id}\t{rec['fixture']}\t{rec['source']}{'' if exists else '  (MISSING)'}")
    return 0


def cmd_rebuild(ns) -> int:
    rc = 0
    for case_id, rec in _load_sources().items():
        if ns.case and ns.case != case_id:
            continue
        source = Path(rec["source"])
        if not source.is_file():
            print(f"{case_id}: source missing: {source}", file=sys.stderr)
            rc = 1
            continue
        if _sha256(source) != rec["sha256"]:
            print(f"{case_id}: WARNING source changed since it was extracted", file=sys.stderr)
        dest = build(case_id, source, rec["args"])
        _record(case_id, source, rec["args"], dest)
        print(f"rebuilt {rec['fixture']}")
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("extract", help="cut an excerpt and record its source locally")
    ex.add_argument("--case", required=True, help="fixture id (directory under fixtures/)")
    ex.add_argument("--source", required=True)
    ex.add_argument("--sheet", action="append", help="xlsx: sheet to keep (repeatable)")
    ex.add_argument("--first", type=int, default=10)
    ex.add_argument("--rows", default="")
    ex.add_argument("--match", action="append", metavar="COL=REGEX")
    ex.add_argument("--max-match", type=int, default=10)
    ex.add_argument("--header-row", type=int, default=1)
    ex.add_argument("--pages", default="", help="pdf: pages to keep, e.g. 3-5")
    ex.set_defaults(fn=cmd_extract)
    sub.add_parser("list", help="show recorded sources").set_defaults(fn=cmd_list)
    rb = sub.add_parser("rebuild", help="re-cut fixtures from the recorded sources")
    rb.add_argument("--case")
    rb.set_defaults(fn=cmd_rebuild)
    ns = ap.parse_args()
    return ns.fn(ns)


if __name__ == "__main__":
    sys.exit(main())

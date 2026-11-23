"""Excel data dictionary extractor for the VLMD conversion pipeline.

Converts one sheet of an Excel workbook to CSV so it can flow through the
normal detect -> convert -> fixup pipeline unchanged. Sheet selection is a
simple heuristic (largest sheet by cell count wins) since most workbooks
mix one real data-dictionary sheet with small notes/legend sheets; pass
--sheet to override when it picks wrong.

Usage (standalone):
    python vlmd_excel.py --input file.xlsx --output work/extracted.csv
    python vlmd_excel.py --input file.xlsx --output work/extracted.csv --sheet "Data Dictionary"

Called automatically by run_pipeline.py when --input ends in .xlsx/.xls.
"""

import argparse
import csv
import sys
from pathlib import Path

import openpyxl


def list_sheets(xlsx_path: str) -> list[tuple[str, int, int]]:
    """Return (sheet_name, row_count, col_count) for every sheet in the workbook."""
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    return [(name, wb[name].max_row or 0, wb[name].max_column or 0) for name in wb.sheetnames]


def _pick_sheet(sheets: list[tuple[str, int, int]]) -> str:
    return max(sheets, key=lambda s: s[1] * s[2])[0]


def extract(xlsx_path: str, output_csv: str, sheet_name: str | None = None) -> int:
    sheets = list_sheets(xlsx_path)
    if not sheets:
        print(f"ERROR: no sheets found in {xlsx_path}", file=sys.stderr)
        return 1

    print("  Workbook sheets: " + ", ".join(f"{n} ({r}x{c})" for n, r, c in sheets), flush=True)

    if sheet_name is None:
        if len(sheets) > 1:
            sheet_name = _pick_sheet(sheets)
            print(
                f"  Multiple sheets found — auto-selected largest: '{sheet_name}' "
                "(pass --sheet to override)",
                flush=True,
            )
        else:
            sheet_name = sheets[0][0]

    names = [s[0] for s in sheets]
    if sheet_name not in names:
        print(f"ERROR: sheet '{sheet_name}' not found. Available: {names}", file=sys.stderr)
        return 1

    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    ws = wb[sheet_name]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        print(f"ERROR: sheet '{sheet_name}' is empty", file=sys.stderr)
        return 1

    header = [str(c).strip() if c is not None else "" for c in rows[0]]
    if not any(header):
        print(
            f"ERROR: sheet '{sheet_name}' has no header row at row 1 — sheets with "
            "preamble rows before the real table aren't supported yet. Pass --sheet "
            "to pick a different sheet, or pre-clean the file.",
            file=sys.stderr,
        )
        return 1

    out_path = Path(output_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    data_rows = 0
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        for row in rows[1:]:
            if all(c is None or str(c).strip() == "" for c in row):
                continue
            # Excel cells routinely carry trailing/leading whitespace or soft
            # line breaks (e.g. "Numeric Values\n") that silently break exact
            # lookups downstream (type mapping, enum values) — strip here.
            writer.writerow(["" if c is None else str(c).strip() for c in row])
            data_rows += 1

    print(f"  Extracted CSV → {out_path}  ({data_rows} data rows)", flush=True)
    return 0


def main():
    ap = argparse.ArgumentParser(description="Convert one sheet of an Excel workbook to CSV.")
    ap.add_argument("--input", required=True, help="Path to input .xlsx/.xls file")
    ap.add_argument("--output", required=True, help="Path for the output CSV file")
    ap.add_argument(
        "--sheet", default=None, help="Sheet name (default: auto-pick the largest sheet)"
    )
    args = ap.parse_args()
    sys.exit(extract(args.input, args.output, sheet_name=args.sheet))


if __name__ == "__main__":
    main()

"""Run all three example conversions and report results.

Demonstrates HBCD, REDCap, and generic-CSV formats. Runs without LLM
credentials (--skip-llm). Output goes to examples/output/.

Usage:
    cd heal-vlmd-pipeline
    python examples/run_examples.py
"""

import sys
from pathlib import Path

# Add pipeline root to import path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from run_pipeline import run

EXAMPLES_DIR = Path(__file__).parent
OUTPUT_DIR = EXAMPLES_DIR / "output"

EXAMPLES = [
    {
        "name": "HBCD",
        "description": "HBCD study data dictionary (25-column CSV with JSON-array levels)",
        "input": str(EXAMPLES_DIR / "hbcd_sample.csv"),
        "format": str(ROOT / "formats" / "hbcd.yaml"),
        "appl_id": "example_hbcd",
        "title": "HBCD Sample — Example",
        "study_label": "HBCD_Sample",
    },
    {
        "name": "REDCap",
        "description": "REDCap data dictionary export (pipe-separated choices)",
        "input": str(EXAMPLES_DIR / "redcap_sample.csv"),
        "format": str(ROOT / "formats" / "redcap.yaml"),
        "appl_id": "example_redcap",
        "title": "REDCap Sample — Example",
        "study_label": "REDCap_Sample",
    },
    {
        "name": "Generic CSV",
        "description": "Plain CSV with common column names (auto-detected by generic-csv.yaml)",
        "input": str(EXAMPLES_DIR / "generic_sample.csv"),
        "format": str(ROOT / "formats" / "generic-csv.yaml"),
        "appl_id": "example_generic",
        "title": "Generic CSV Sample — Example",
        "study_label": "Generic_Sample",
    },
]


def main():
    print("=" * 56)
    print(" VLMD Pipeline — Example Conversions")
    print(f" Output directory: {OUTPUT_DIR}")
    print("=" * 56)

    results = []
    for ex in EXAMPLES:
        print(f"\n{'─' * 56}")
        print(f"  {ex['name']}: {ex['description']}")
        print(f"{'─' * 56}")

        rc = run(
            input_file=ex["input"],
            format_yaml=ex["format"],
            appl_id=ex["appl_id"],
            hdp_id="",  # empty → no copy to CleanedDataDictionaries
            title=ex["title"],
            study_label=ex["study_label"],
            model="azure-gpt-4.1-mini",
            skip_llm=True,  # no credentials needed for examples
            no_detect=True,  # format explicitly specified
            output_dir=OUTPUT_DIR / ex["appl_id"],
        )
        results.append((ex["name"], rc))

    print(f"\n{'=' * 56}")
    print(" Results")
    print(f"{'=' * 56}")
    all_ok = True
    for name, rc in results:
        status = "PASS" if rc == 0 else f"FAIL (exit {rc})"
        print(f"  {name:<20} {status}")
        if rc != 0:
            all_ok = False

    if all_ok:
        print(f"\n  All examples passed. VLMD files in {OUTPUT_DIR}/")
    else:
        print("\n  One or more examples failed — check output above.")
    print("=" * 56)
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()

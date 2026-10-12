"""End-to-end offline conversions of the synthetic examples in examples/.

The same runs as examples/run_examples.py: --skip-llm --no-detect with the
format given explicitly. run() validates the merged output against the VLMD
schema and returns 2 if it fails, so rc == 0 means schema-valid output.
"""

import csv
import json
from pathlib import Path

import pytest
from run_examples import EXAMPLES

import run_pipeline

# One categorical field per example, so a broken levels parser fails the test
# even though its output would still be schema-valid.
EXPECTED_LEVELS = {
    "HBCD": ("sex_assigned_birth", {"1": "Male", "2": "Female", "3": "Intersex"}),
    "REDCap": (
        "sex",
        {"1": "Male", "2": "Female", "3": "Non-binary", "4": "Prefer not to answer"},
    ),
    "Generic CSV": ("biological_sex", {"1": "Male", "2": "Female", "3": "Intersex"}),
}


@pytest.mark.parametrize("example", EXAMPLES, ids=[ex["name"] for ex in EXAMPLES])
def test_example_converts_and_validates(example, tmp_path):
    output_dir = tmp_path / "output"
    rc = run_pipeline.run(
        input_file=example["input"],
        format_yaml=example["format"],
        appl_id=example["appl_id"],
        hdp_id="",
        title=example["title"],
        study_label=example["study_label"],
        skip_llm=True,
        no_detect=True,
        output_dir=output_dir,
    )
    assert rc == 0

    stem = Path(example["input"]).stem
    vlmd_dir = output_dir / "vlmd" / stem
    vlmd_json = vlmd_dir / f"{stem}.vlmd.json"
    assert vlmd_json.exists()
    assert (vlmd_dir / f"{stem}.vlmd.csv").exists()
    assert (output_dir / "input" / Path(example["input"]).name).exists()

    doc = json.loads(vlmd_json.read_text(encoding="utf-8"))
    with open(example["input"], newline="", encoding="utf-8") as f:
        input_rows = list(csv.DictReader(f))
    assert len(doc["fields"]) == len(input_rows)

    name, labels = EXPECTED_LEVELS[example["name"]]
    field = next(f for f in doc["fields"] if f["name"] == name)
    assert field["constraints"]["enum"] == list(labels)
    assert field["enumLabels"] == labels

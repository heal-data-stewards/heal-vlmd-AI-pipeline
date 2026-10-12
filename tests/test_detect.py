"""Unit tests for vlmd_detect: picking a format YAML for an input file.

Put tests here for rule-based detection (scoring a header against each
format's signature columns), and for checks that every YAML in formats/ is
usable by detection. Tests never call the LLM fallback: pass
llm_fallback=False.
"""

from pathlib import Path

import pytest

import vlmd_detect

ROOT = Path(__file__).parent.parent
EXAMPLES_DIR = ROOT / "examples"


# --- Detecting the example files ---
# Each synthetic file in examples/ is detected as the format it was written
# for, without the LLM.


@pytest.mark.parametrize(
    ("sample", "expected"),
    [
        ("hbcd_sample.csv", "hbcd"),
        ("redcap_sample.csv", "redcap"),
        # generic-csv is never auto-detected (confidence_threshold > 1); it is
        # only used when named explicitly, so this falls through to "unknown".
        ("generic_sample.csv", None),
    ],
)
def test_detect_examples_without_llm(sample, expected):
    result = vlmd_detect.detect(str(EXAMPLES_DIR / sample), llm_fallback=False)
    assert result["format_name"] == expected
    if expected:
        assert result["method"] == "rule_based"
        assert not result["ambiguous"]


# --- The format YAMLs ---
# Every file in formats/ loads and has what detection needs. Add a check here
# when detection starts relying on a new YAML key.


def test_every_format_yaml_loads():
    """A YAML that fails to load is only a warning at detection time, so check here."""
    yaml_files = sorted((ROOT / "formats").glob("*.yaml"))
    specs = vlmd_detect.load_all_formats()
    # Fewer specs than files means a load failure or two files sharing a format_name.
    assert len(specs) == len(yaml_files)
    for name, spec in specs.items():
        assert "name_column" in spec, name
        assert "signature_columns" in spec.get("detection", {}), name


# --- Scoring a header against a format ---
# score_format() on a hand-made spec, so the weighting is tested independently
# of the real YAMLs.

SPEC = {"detection": {"signature_columns": {"strong": ["a", "b"], "supporting": ["c", "d"]}}}


@pytest.mark.parametrize(
    ("columns", "expected"),
    [
        ({"a", "b", "c", "d"}, 1.0),
        ({"x", "y"}, 0.0),
        # Strong columns weigh twice as much as supporting ones: (2*1 + 1) / 6.
        ({"a", "c"}, 0.5),
        ({"c", "d"}, 2 / 6),
    ],
)
def test_score_format(columns, expected):
    assert vlmd_detect.score_format(columns, SPEC) == pytest.approx(expected)


def test_score_format_without_signature_columns():
    assert vlmd_detect.score_format({"a"}, {}) == 0.0
    empty = {"detection": {"signature_columns": {"strong": [], "supporting": []}}}
    assert vlmd_detect.score_format({"a"}, empty) == 0.0

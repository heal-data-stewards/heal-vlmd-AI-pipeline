import pytest

from vlmd_convert import parse_levels

JSON_ARRAY = {"format": "json_array", "value_key": "value", "label_key": "label"}
PIPE = {"format": "pipe_separated", "pair_separator": ",", "choice_separator": "|"}


def test_json_array():
    raw = '[{"label": "Male", "value": "1"}, {"label": "Female", "value": "2"}]'
    assert parse_levels(raw, JSON_ARRAY) == (["1", "2"], {"1": "Male", "2": "Female"})


def test_pipe_separated():
    assert parse_levels("1, Yes | 0, No", PIPE) == (["1", "0"], {"1": "Yes", "0": "No"})


def test_comma_separated():
    assert parse_levels("red, green, blue", {"format": "comma_separated"}) == (
        ["red", "green", "blue"],
        {"red": "red", "green": "green", "blue": "blue"},
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('[{"label": "Yes", "value": "1"}]', (["1"], {"1": "Yes"})),
        ("1, Yes | 0, No", (["1", "0"], {"1": "Yes", "0": "No"})),
    ],
)
def test_auto_detect(raw, expected):
    assert parse_levels(raw, {"format": "auto_detect"}) == expected


@pytest.mark.parametrize("raw", ["", "nan", "NaN", "  "])
def test_blank_input_has_no_levels(raw):
    assert parse_levels(raw, JSON_ARRAY) == ([], {})


def test_no_spec_or_unknown_format_has_no_levels():
    assert parse_levels("1, Yes", None) == ([], {})
    assert parse_levels("1, Yes", {"format": "not-a-format"}) == ([], {})

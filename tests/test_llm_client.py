import json

import pytest

from llm_client import parse_json_response


@pytest.mark.parametrize(
    "text",
    [
        '[{"name": "x"}]',
        '  [{"name": "x"}]\n',
        '```json\n[{"name": "x"}]\n```',
        '```\n[{"name": "x"}]\n```',
        '\n```json\n[{"name": "x"}]\n```\n',
    ],
)
def test_parse_json_response_strips_fences(text):
    assert parse_json_response(text) == [{"name": "x"}]


def test_parse_json_response_rejects_prose():
    with pytest.raises(json.JSONDecodeError):
        parse_json_response("Here is the JSON: []")

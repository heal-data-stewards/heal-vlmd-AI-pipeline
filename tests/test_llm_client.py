"""Unit tests for llm_client's offline helpers.

Put tests here for code in llm_client.py that runs without a network call or
credentials, such as parsing a model's reply. Nothing here may call
call_llm() or construct a client: CI has no LLM credentials.
"""

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

"""Unit tests for llm_client's offline helpers.

Put tests here for code in llm_client.py that runs without a network call or
credentials, such as parsing a model's reply. Nothing here may call
call_llm() or construct a real client: CI has no LLM credentials. To test a
_call_* function, monkeypatch its _get_*_client with a stand-in.
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


# --- Replies with no content ---
# A content-filtered or refused reply becomes an error result that says so. The
# SDK client is replaced by a stand-in, so nothing is sent anywhere.


def test_azure_reply_with_no_content_is_an_error(monkeypatch):
    from types import SimpleNamespace

    import llm_client

    choice = SimpleNamespace(message=SimpleNamespace(content=None), finish_reason="content_filter")
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kw: SimpleNamespace(choices=[choice]))
        )
    )
    monkeypatch.setattr(llm_client, "_get_azure_client", lambda: fake_client)

    text, ok, err = llm_client._call_azure("sys", "user", {"model_id": "m"}, 10, 0.0)

    assert (text, ok) == ("", False)
    assert "content_filter" in err


def test_azure_reply_with_no_choices_is_an_error(monkeypatch):
    from types import SimpleNamespace

    import llm_client

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kw: SimpleNamespace(choices=[]))
        )
    )
    monkeypatch.setattr(llm_client, "_get_azure_client", lambda: fake_client)

    text, ok, err = llm_client._call_azure("sys", "user", {"model_id": "m"}, 10, 0.0)

    assert (text, ok) == ("", False)
    assert err.startswith(llm_client.NO_CONTENT_ERROR)


def fake_anthropic(monkeypatch, content, stop_reason="end_turn"):
    from types import SimpleNamespace

    import llm_client

    resp = SimpleNamespace(content=content, stop_reason=stop_reason)
    fake_client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: resp))
    monkeypatch.setattr(llm_client, "_get_anthropic_client", lambda: fake_client)


def test_anthropic_reply_skips_blocks_that_are_not_text(monkeypatch):
    from types import SimpleNamespace

    import llm_client

    thinking = SimpleNamespace(type="thinking", thinking="hmm")
    text_block = SimpleNamespace(type="text", text=" [] ")
    fake_anthropic(monkeypatch, [thinking, text_block])

    assert llm_client._call_anthropic("sys", "user", {"model_id": "m"}, 10, 0.0) == ("[]", True, "")


@pytest.mark.parametrize("blocks", [[], ["thinking"]])
def test_anthropic_reply_with_no_text_is_an_error(monkeypatch, blocks):
    from types import SimpleNamespace

    import llm_client

    fake_anthropic(monkeypatch, [SimpleNamespace(type=b) for b in blocks], "refusal")

    text, ok, err = llm_client._call_anthropic("sys", "user", {"model_id": "m"}, 10, 0.0)

    assert (text, ok) == ("", False)
    assert err.startswith(llm_client.NO_CONTENT_ERROR)
    assert "refusal" in err

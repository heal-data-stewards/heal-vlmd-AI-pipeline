"""Unit tests for vlmd_fixup: LLM repair of lint-flagged fields.

Put tests here for fixup() and its helpers. The LLM is never called:
vlmd_fixup.call_llm (or process_chunk) is monkeypatched, so these tests cover
what fixup does with the model's replies, not the prompts.
"""

import json

import vlmd_fixup


def test_retry_after_unparseable_reply_keeps_the_token_limit(monkeypatch):
    # The usual reason a reply doesn't parse is that it was cut off at the limit, so a
    # retry with a lower limit would be cut off sooner.
    limits = []
    replies = iter(['[{"field": {"name": "a"}, "justifi', json.dumps([{"field": {"name": "a"}}])])

    def fake_call_llm(system, user, model_key, max_tokens):
        limits.append(max_tokens)
        return next(replies), True, ""

    monkeypatch.setattr(vlmd_fixup, "call_llm", fake_call_llm)

    result = vlmd_fixup.process_chunk("sys", "prompt", [{"name": "a"}], "test-model")

    assert result == [{"field": {"name": "a"}}]
    assert limits == [vlmd_fixup.MAX_TOKENS_OUT, vlmd_fixup.MAX_TOKENS_OUT]

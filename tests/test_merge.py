"""Unit tests for vlmd_merge: applying LLM fixes to the converted fields.

Put tests here for merge_fixes() and the steps that shape the final output
(normalize_fields, fields_to_csv_rows), called directly on small inline field
lists. A test that
needs the output files written to disk belongs in test_examples.py.
"""

from vlmd_merge import merge_fixes


def test_merge_fixes_updates_only_the_keys_in_the_fix():
    fields = [
        {"name": "a", "description": "old", "section": "Demographics"},
        {"name": "b", "description": "untouched"},
    ]
    merged = merge_fixes(fields, [{"name": "a", "description": "new"}])
    assert merged == [
        {"name": "a", "description": "new", "section": "Demographics"},
        {"name": "b", "description": "untouched"},
    ]


def test_merge_fixes_ignores_unknown_or_unnamed_fixes():
    fields = [{"name": "a", "description": "old"}]
    merged = merge_fixes(fields, [{"name": "zzz", "description": "x"}, {"description": "y"}])
    assert merged == [{"name": "a", "description": "old"}]

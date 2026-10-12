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

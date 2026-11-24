"""Data-dictionary test suite: runs each manifest case through the offline stages and
checks the properties in its `expect` block. See README.md for what each fixture covers.

Every assertion in a case is its own test (`test_check[<case>::<check>]`), so a case can be
partly passing; a check the pipeline can't pass yet is listed under the case's `xfail` map.

    pytest tests/suite                 # offline cases
    pytest tests/suite --run-local     # also cases that read files from sources.local.yaml
    pytest tests/suite --run-llm       # also cases that call an LLM
"""

import pytest
import suite_lib


def _params():
    params = []
    for case in suite_lib.load_manifest():
        for check_id in suite_lib.check_ids(case):
            marks = []
            reason = (case.get("xfail") or {}).get(check_id)
            if case.get("status") == "xfail":
                reason = reason or case["xfail_reason"]
            if reason:
                marks.append(pytest.mark.xfail(strict=True, reason=reason))
            params.append(pytest.param(case, check_id, id=f"{case['id']}::{check_id}", marks=marks))
    return params


@pytest.fixture(scope="session")
def runs(tmp_path_factory):
    """Each case is run through the pipeline once and shared by all of its checks."""
    cache: dict[str, dict] = {}

    def get(case):
        if case["id"] not in cache:
            cache[case["id"]] = suite_lib.run_offline(case, tmp_path_factory.mktemp(case["id"]))
        return cache[case["id"]]

    return get


@pytest.mark.parametrize("case,check_id", _params())
def test_check(case, check_id, runs, request):
    tier = case.get("tier", "offline")
    if tier == "llm" and not request.config.getoption("--run-llm"):
        pytest.skip("needs LLM credentials; pass --run-llm")
    if tier == "local" and not request.config.getoption("--run-local"):
        pytest.skip("needs local source files; pass --run-local")
    if tier != "offline":
        pytest.skip(f"tier {tier!r} has no runner yet")

    errors = suite_lib.check_one(case, runs(case), check_id)
    assert not errors, "; ".join(errors)


def test_manifest_is_consistent():
    modes = suite_lib.load_failure_modes()
    ids = set()
    for case in suite_lib.load_manifest():
        assert case["id"] not in ids, f"duplicate case id {case['id']}"
        ids.add(case["id"])
        unknown = set(case.get("covers", [])) - set(modes)
        assert not unknown, f"{case['id']}: unknown failure-mode ids {unknown}"
        assert case.get("covers"), f"{case['id']}: covers is empty"
        assert case.get("expect"), f"{case['id']}: expect is empty"
        if case.get("status") == "xfail":
            assert case.get("xfail_reason"), f"{case['id']}: xfail needs xfail_reason"
        stale = set(case.get("xfail") or {}) - set(suite_lib.check_ids(case))
        assert not stale, f"{case['id']}: xfail entries match no check: {stale}"
        if case.get("tier", "offline") == "offline":
            assert suite_lib.fixture_path(case).exists(), f"{case['id']}: fixture file missing"


def test_readme_is_up_to_date():
    import build_readme

    assert build_readme.render(build_readme.README.read_text(encoding="utf-8")) == (
        build_readme.README.read_text(encoding="utf-8")
    ), "tests/suite/README.md is out of date; run: python tests/suite/build_readme.py"

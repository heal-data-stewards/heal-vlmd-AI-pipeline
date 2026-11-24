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
def runs(tmp_path_factory, request):
    """Each case is run once and shared by all of its checks: offline cases through the
    offline stages, llm cases through the whole pipeline with the LLM steps on."""
    cache: dict[str, dict] = {}

    def get(case):
        if case["id"] not in cache:
            work = tmp_path_factory.mktemp(case["id"])
            if case.get("tier", "offline") == "llm":
                cache[case["id"]] = suite_lib.run_pipeline_case(case, work, skip_llm=False)
            else:
                cache[case["id"]] = suite_lib.run_offline(case, work)
        return cache[case["id"]]

    return get


@pytest.mark.parametrize("case,check_id", _params())
def test_check(case, check_id, runs, request):
    tier = case.get("tier", "offline")
    if tier == "llm" and not request.config.getoption("--run-llm"):
        pytest.skip("needs LLM credentials; pass --run-llm")
    if tier == "local" and not request.config.getoption("--run-local"):
        pytest.skip("needs local source files; pass --run-local")
    if tier == "local":
        pytest.skip("tier 'local' has no runner yet")

    errors = suite_lib.check_one(case, runs(case), check_id)
    assert not errors, "; ".join(errors)


def test_pipeline_runner_without_llm(tmp_path):
    """The runner used for tier-llm cases works end to end; checked with the LLM steps off."""
    case = next(c for c in suite_lib.load_manifest() if c["id"] == "redcap-slider-minimal")
    run = suite_lib.run_pipeline_case(case, tmp_path, skip_llm=True)
    assert run["pipeline_rc"] in (0, 2), run
    assert len(run["fields"]) == 11
    assert not suite_lib.check_one(case, run, "fields_out")


def test_synthetic_fixtures_match_generator():
    import make_synthetic

    stale = [
        case_id
        for case_id, gen in make_synthetic.GENERATORS.items()
        if (make_synthetic.FIXTURES / case_id / "input.csv").read_bytes() != gen()
    ]
    assert not stale, f"edit make_synthetic.py, not the fixtures; out of date: {stale}"


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
        if case.get("tier", "offline") != "local":
            assert suite_lib.fixture_path(case).exists(), f"{case['id']}: fixture file missing"


def test_readme_is_up_to_date():
    import build_readme

    assert build_readme.render(build_readme.README.read_text(encoding="utf-8")) == (
        build_readme.README.read_text(encoding="utf-8")
    ), "tests/suite/README.md is out of date; run: python tests/suite/build_readme.py"

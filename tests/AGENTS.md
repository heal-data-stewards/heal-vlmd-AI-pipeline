# tests/AGENTS.md

Guidance for adding and organizing tests. Run them with `pytest` from the repo root. They are offline, need no LLM credentials, and CI runs them on every pull request.

## Where a test goes

- **Read the header docstring of each test file first.** Each one says what belongs in that file. Put a new test in the file whose header covers it.
- **One file per module, for unit tests:** `test_<module>.py` tests `<module>.py` (`test_detect.py` → `vlmd_detect.py`). Start a new file the first time a module gets a test.
- **End-to-end runs of `run_pipeline.run()` go in `test_examples.py`.** It runs the same `--skip-llm --no-detect` conversions as `examples/run_examples.py`, reusing its `EXAMPLES` list. It also spot-checks one categorical field per example, because a broken levels parser still produces schema-valid output. Regression fixtures from real conversions (#13) belong in `tests/` too, so CI runs them.
- **Shared fixtures go in `conftest.py`**, and only when more than one file needs them or they must apply to every test.

## Writing a test file

- **Start every file with a module docstring** that says which module or behaviour it tests and what sort of test belongs there. Write it for the next person deciding where a test goes, not as a summary of the tests already in it.
- **Name each group of related tests** once a file tests more than one function or behaviour. Use a comment heading and a line or two saying what the group covers:

  ```python
  # --- Scoring a header against a format ---
  # score_format() on a hand-made spec, so the weighting is tested independently
  # of the real YAMLs.
  ```

  Grep for `# ---` to list a file's groups. Use comments rather than test classes, so test IDs and `-k` filters stay as they are. A file that tests one function needs no group heading; give it one when a second group arrives.
- **Never call an LLM.** CI has no credentials. Test the code around an LLM call (prompt building, response parsing) directly, and pass `llm_fallback=False` / `skip_llm=True` to stage functions.

## Keeping tests out of the repo

`conftest.py` monkeypatches `run_pipeline.WORK_DIR` to a temp dir for every test. `output_dir` is a parameter, not a module global, so any test calling `run()` must also pass `output_dir=tmp_path / ...`, or it writes into `output/`.

`pytest.ini` puts the repo root and `examples/` on `sys.path`, since the repo is flat scripts rather than a package: import stage modules directly (`import vlmd_detect`).

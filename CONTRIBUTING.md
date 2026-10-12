# Contributing

Thanks for helping improve the VLMD conversion pipeline. This file covers how to set up a development environment and what a pull request needs before it is ready for review. For what the pipeline does and how to use it, see the [README](README.md); for how the code is put together, see the Architecture section of [AGENTS.md](AGENTS.md).

## Setting up

```bash
git clone https://github.com/heal-data-stewards/heal-vlmd-AI-pipeline
cd heal-vlmd-AI-pipeline
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt     # requirements.txt + pytest + the pinned Ruff version
pip install pyreadstat                  # only if you work on Stata (.dta) input
cp .env.template .env                   # only if you work on the LLM steps
git config blame.ignoreRevsFile .git-blame-ignore-revs   # once: skip bulk reformat commits in git blame
```

Python 3.11 or later is required. Conversion, detection and validation run without LLM credentials; only the fixup, description triage, PDF extraction and unknown-format steps need them.

## Before opening a pull request

GitHub Actions (`.github/workflows/ci.yml`) runs the first two steps on every pull request, and a pull request isn't ready for review until they pass. Running them locally first is faster than waiting for CI.

1. **Format and lint the Python code with Ruff.**

   ```bash
   python -m ruff format .
   python -m ruff check .
   ```

   Run these before you open the pull request, and again before you ask for another round of review. CI runs `ruff format --check .`, which fails on any file that needs formatting. Besides `.py` files, Ruff also formats the Python code blocks in Markdown files such as this one and the README. `python -m ruff check --fix .` applies the fixes Ruff is sure of; read them before committing. A deliberate `except Exception` at a stage boundary (an LLM call, the validator, a PDF read) is marked `# noqa: BLE001` with a reason rather than narrowed, because narrowing it would turn an exit code into a traceback.

   Run Ruff as `python -m ruff` from the virtual environment, so you get the version pinned in `requirements-dev.txt`. A bare `ruff` may be a different version installed elsewhere (Homebrew, pipx), and other versions format some code differently and will produce unrelated changes. Without the virtual environment, `uvx ruff@0.17.0` runs the pinned version.

2. **Run the tests.**

   ```bash
   pytest
   ```

   The tests run offline and need no credentials. They include the example conversions in `examples/`, which convert three synthetic dictionaries and validate the output against the VLMD schema. `python examples/run_examples.py` runs the same conversions with readable output. Add tests for what you change; a test that calls `run_pipeline.run()` must pass `output_dir=tmp_path / ...` so nothing is written into the repository. If you changed an LLM step, which the tests don't call, also run the pipeline on a file that exercises that step, and say in the pull request what you ran.

3. **Update the README** if you changed a command-line flag, the format YAML anatomy, the `levels.format` values, the LLM models table or the exit codes. The README is the user manual and has to stay in sync with the code.

## Guidelines

- **Add a format as YAML, not Python.** A new input format is usually a new file in `formats/`, plus a prompt in `prompts/` if it needs one; see "Adding a new format" in the README. Write Python only when the input can't be described by a YAML mapping, as with PDF and Excel.
- **Keep pipeline logic in the stage modules** (`vlmd_*.py`), not in `run_pipeline.py` or `chat_pipeline.py`, which both drive the same stages.
- **Treat exit codes and the JSON files in `work/` as a public interface.** Bots drive the pipeline through them, so changing what a code means or how a file is laid out is a breaking change. Call it out in the pull request.
- **Never commit participant data or credentials.** `.env`, `work/` and `output/` are gitignored; check `git status` before committing anything from them. Use synthetic data dictionaries for examples, and before committing a real one (as a test fixture, say), confirm that the study's data dictionary is cleared for sharing. The study-specific mappings in `formats/` (`{applid}.yaml`, `HDP*.yaml`) are committed on purpose: they record a column mapping a person confirmed and contain no data values.
- **Bump Ruff deliberately.** Move to a new Ruff version in one pull request with three commits: first change the pin in both `requirements-dev.txt` and `.github/workflows/ci.yml`, with fixes for anything new that `ruff check` reports; then run `python -m ruff format .` and commit the result as its own commit containing nothing else; then add that commit's full SHA to `.git-blame-ignore-revs` so `git blame` skips it. Keeping the pin change out of the reformat commit means `git blame` still shows who changed the pin.

## Pull requests

- Keep each pull request to one change, and split it into commits that each make sense on their own; reformatting and behaviour changes belong in separate commits.
- Write the title as a description of what the change does, e.g. "Detect REDCap exports with renamed columns", since titles become entries in the release notes.
- Anything a future developer needs to know belongs in the repository (code comments, the README, `AGENTS.md`), not only in the pull request description. That includes approaches that were tried and didn't work, if someone is likely to try them again.
- Every pull request needs a review from a person before it is merged.
- Pull requests are merged with a merge commit; the repository's settings turn off squash and rebase merging. Keep it that way: a squash or rebase gives every commit a new SHA, so the commits listed in `.git-blame-ignore-revs` wouldn't exist on `main` and `git blame` would silently stop skipping them.

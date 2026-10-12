# Contributing

Thanks for helping improve the VLMD conversion pipeline. This file covers how to set up a development environment and what a pull request needs before it is ready for review. For what the pipeline does and how to use it, see the [README](README.md); for how the code is put together, see the Architecture section of [AGENTS.md](AGENTS.md).

## Setting up

```bash
git clone https://github.com/heal-data-stewards/heal-vlmd-AI-pipeline
cd heal-vlmd-AI-pipeline
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt     # requirements.txt + the pinned Ruff version
pip install pyreadstat                  # only if you work on Stata (.dta) input
cp .env.template .env                   # only if you work on the LLM steps
git config blame.ignoreRevsFile .git-blame-ignore-revs   # once: skip bulk reformat commits in git blame
```

Python 3.11 or later is required. Conversion, detection and validation run without LLM credentials; only the fixup, description triage, PDF extraction and unknown-format steps need them.

## Before opening a pull request

1. **Format the Python code with Ruff.**

   ```bash
   ruff format .
   ```

   Run this before you open the pull request, and again before you ask for another round of review. `ruff format --check .` reports files that need formatting without changing them. Use the Ruff version pinned in `requirements-dev.txt`: other versions format some code differently and will produce unrelated changes.

2. **Run the examples.**

   ```bash
   python examples/run_examples.py
   ```

   This converts the three synthetic dictionaries in `examples/` with no LLM calls and validates the output against the VLMD schema. It is the only automated check in the repository, so run it after changing any pipeline stage. Exit 0 means all three passed. If you changed an LLM step, also run the pipeline on a file that exercises that step, and say in the pull request what you ran.

3. **Update the README** if you changed a command-line flag, the format YAML anatomy, the `levels.format` values, the LLM models table or the exit codes. The README is the user manual and has to stay in sync with the code.

## Guidelines

- **Add a format as YAML, not Python.** A new input format is usually a new file in `formats/`, plus a prompt in `prompts/` if it needs one; see "Adding a new format" in the README. Write Python only when the input can't be described by a YAML mapping, as with PDF and Excel.
- **Keep pipeline logic in the stage modules** (`vlmd_*.py`), not in `run_pipeline.py` or `chat_pipeline.py`, which both drive the same stages.
- **Treat exit codes and the JSON files in `work/` as a public interface.** Bots drive the pipeline through them, so changing what a code means or how a file is laid out is a breaking change. Call it out in the pull request.
- **Never commit real study data or credentials.** Use synthetic data in examples and tests. `.env`, `work/` and `output/` are gitignored; check `git status` before committing anything from them. The study-specific mappings in `formats/` (`{applid}.yaml`, `HDP*.yaml`) are the exception: they record a column mapping a person confirmed, contain no data values, and are committed on purpose.
- **Bump Ruff deliberately.** To move to a new Ruff version, change the pin in `requirements-dev.txt`, run `ruff format .` and commit the result as its own commit containing nothing else. Then add that commit's full SHA to `.git-blame-ignore-revs` so `git blame` skips it.

## Pull requests

- Keep each pull request to one change, and split it into commits that each make sense on their own; reformatting and behaviour changes belong in separate commits.
- Write the title as a description of what the change does, e.g. "Detect REDCap exports with renamed columns", since titles become entries in the release notes.
- Anything a future developer needs to know belongs in the repository (code comments, the README, `AGENTS.md`), not only in the pull request description. That includes approaches that were tried and didn't work, if someone is likely to try them again.
- Every pull request needs a review from a person before it is merged.
- A pull request that adds a SHA to `.git-blame-ignore-revs` must be merged with a merge commit, not squashed or rebased; otherwise the listed SHA won't exist on `main`.

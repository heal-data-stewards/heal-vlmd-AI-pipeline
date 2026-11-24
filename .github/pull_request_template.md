<!-- Title: what the change does, e.g. "Detect REDCap exports with renamed columns". -->

## Linked issue

Closes #

<!--
Required: the `linked-issue` check fails unless this pull request closes an issue, through a closing keyword
(`Closes`, `Fixes` or `Resolves`) here or an issue linked in the Development sidebar. Prefer the keyword: reviewers see it.
- An issue in the other repository: `Closes heal-data-stewards/vlmd-app#N`.
- No issue for this work yet? Create one first with the issue forms (Bug, Feature or Task), make sure it is on the board
  (https://github.com/orgs/heal-data-stewards/projects/1), then link it here. Work without an issue is invisible on the board.
- Stacked on another pull request? This pull request still needs its own issue; split the original into sub-issues
  if necessary. See "Stacked pull requests" in DEVELOPMENT.md.
-->

## What changed

<!-- What and why, briefly. Call out anything a reviewer might want to overrule, and any change to exit codes or work/ files (a public interface). -->

## How it was tested

<!-- Commands you ran and their results. If you changed an LLM step, say which file you ran it on. -->

- [ ] `python -m ruff format .` and `python -m ruff check .`
- [ ] Tests / `python examples/run_examples.py` pass

## Documentation

- [ ] README updated if CLI flags, format YAML anatomy, `levels.format` values, the models table or exit codes changed
- [ ] `AGENTS.md`, `CONTRIBUTING.md` or `DEVELOPMENT.md` updated if the architecture or workflow changed
- [ ] No study data or credentials committed

<!-- Stacked? Say what this is based on and the merge order. -->

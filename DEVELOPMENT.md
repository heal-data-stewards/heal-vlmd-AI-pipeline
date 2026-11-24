# Development workflow

This file is how work moves from an idea to `main`, for people and for AI agents. It applies to both repositories on the [VLMD AI Tool Development board](https://github.com/orgs/heal-data-stewards/projects/1): [`heal-vlmd-AI-pipeline`](https://github.com/heal-data-stewards/heal-vlmd-AI-pipeline) (this one) and [`vlmd-app`](https://github.com/heal-data-stewards/vlmd-app). For setting up a development environment and what a pull request needs technically (Ruff, tests, README updates), see [CONTRIBUTING.md](CONTRIBUTING.md).

The short version:

1. Every piece of work starts as an **issue on the board**.
2. Work on a **branch named after the issue**: `type/issue-N-slug`.
3. Open a **pull request into `main`** that says `Closes org/repo#N`.
4. CI passes, **one person approves**, and the pull request is **merged with a merge commit**. The issue closes and the board moves it to Done.

## Issues

An issue is the unit of work. Open one with the **Bug**, **Feature** or **Task** form; blank issues are turned off so every issue has the same shape. Each form asks for:

- **Context**: what the problem is and why it matters, with links to related issues, Slack threads or the study's HDP ID. Never paste or attach a study's data or data dictionary unless it is cleared for sharing.
- **Acceptance criteria**: a checklist someone else, or an agent, can check the finished work against. If you can't write one, the issue isn't ready to be worked on yet.

Write cross-repository references in full, `heal-data-stewards/vlmd-app#11`, because issue numbers repeat between the two repositories.

Large pieces of work are **epics** with **sub-issues**. A sub-issue should be small enough for one pull request.

**`agent-ready` label.** Add it when an issue is specified well enough for an autonomous agent to pick up and finish without asking questions: the context names the files or behaviour involved, the acceptance criteria are checkable, and any decisions are already made. Remove it if a question comes up.

## The board

Every issue in both repositories belongs on the [board](https://github.com/orgs/heal-data-stewards/projects/1). New issues and pull requests are added automatically; if one is missing, add it by hand.

- **Phase** says what kind of work an item is (Phase 0 infrastructure and setup through Phase 5 later). Use the Phase field, not labels.
- **Status**:

  | Status | Meaning | Moved by |
  |---|---|---|
  | Backlog | Not started, or not yet scoped | Default for new items |
  | Ready | Scoped, with acceptance criteria; can be picked up | A person |
  | In progress | Someone is working on it; a draft pull request may exist | A person, or a draft pull request opening |
  | Needs review | A pull request is open and waiting for a reviewer | A pull request opening or leaving draft |
  | In review | A reviewer has commented, approved or requested changes | A review being submitted |
  | Done | The pull request merged into `main` and the issue closed | The merge |

  The "Moved by" automation is `.github/workflows/project-status.yml`. It finds the issues a pull request closes (from `Closes #N` in its description and from the issues GitHub links to it) and moves them forward, never back, except that turning a pull request back into a draft returns its issues to In progress.

- Assign yourself when you move an item to In progress, so two people (or agents) don't pick up the same issue.

## Branches

We use [GitHub Flow](https://docs.github.com/en/get-started/using-github/github-flow): `main` is the only long-lived branch, and it is always releasable. Work happens on short-lived branches that are deleted when they merge.

Name a branch `type/issue-N-slug`:

```
feat/issue-54-mcp-auth
fix/issue-45-xls-reader
docs/issue-88-development-md
```

- `N` is the issue number in the repository the branch is in.
- `slug` is two to five lowercase words joined by hyphens.
- `type` is one of:

  | Type | For |
  |---|---|
  | `feat` | New behaviour |
  | `fix` | A bug fix |
  | `docs` | Documentation only |
  | `test` | Tests or fixtures only |
  | `refactor` | A change in structure with no change in behaviour |
  | `chore` | Tooling, CI, dependencies, repository settings |

One branch is for one issue. Dependabot's branches (`dependabot/...`) are the only exception.

The name is a convention; no check enforces it. GitHub's **Create a branch** button on an issue suggests `N-issue-title`; change it to `type/issue-N-slug` in the dialog before creating the branch. From the command line, `gh issue develop N --name type/issue-N-slug --checkout` does the same. Either way the branch is linked to the issue, and so is a pull request opened from it.

## Pull requests

- **Target `main`.** Open the pull request as a **draft** while the work is in progress, and mark it ready for review when CI passes and you've checked it against the issue's acceptance criteria.
- **Link the issue.** Write `Closes #N` (or `Fixes`/`Resolves`) in the description, or `Closes heal-data-stewards/vlmd-app#N` for an issue in the other repository. An issue linked by GitHub also counts: one linked in the pull request's Development sidebar, or the issue a branch was made from with **Create a branch**. The `linked-issue` check fails if there is neither. Linking in the sidebar doesn't start a new run of the check, so re-run it afterwards. Prefer writing `Closes #N` anyway: it's visible to reviewers, and it's the only link a stacked pull request has. If there is no issue for the work, create one first with the issue forms and make sure it is on the board; a pull request without an issue is invisible on the board.
- **Fill in the template**: what changed, how it was tested, and which documentation was updated.
- Follow the commit and pull request rules in [CONTRIBUTING.md](CONTRIBUTING.md#pull-requests): one change per pull request, commits that make sense on their own, and a title that describes what the change does.

### Stacked pull requests

A pull request may be based on another pull request's branch when it builds on unmerged work. GitHub only links `Closes #N` to an issue when the pull request targets `main`, so a stacked pull request doesn't close its issue by itself until GitHub retargets it to `main`, which happens when the pull request below it merges and its branch is deleted. To keep the board accurate:

- **Every pull request in a stack closes its own issue.** If one issue is being delivered by several stacked pull requests, split it into sub-issues, one per pull request, before opening them.
- Say in the description what the pull request is stacked on, and the merge order.
- Merge from the bottom of the stack up. Don't merge a stacked pull request into its base branch; wait for it to be retargeted to `main` and merge it there.

## Review and merge

`main` is protected by a ruleset. A pull request can merge when:

- the `lint`, `test` and `linked-issue` checks pass,
- the branch is up to date with `main`, and
- **one person has approved it.** With one approval, it is OK to merge (team decision, 2026-11-23). The author merges, unless the author is an agent.

Merge with **Create a merge commit**; squash and rebase merging are turned off (see [CONTRIBUTING.md](CONTRIBUTING.md#pull-requests) for why). The branch is deleted automatically.

`.github/CODEOWNERS` requests a review from the `@heal-data-stewards/vlmd-devs` team on every pull request. Any member's approval counts.

## Working with AI agents

Agents (Claude Code, Copilot and others) follow the same workflow, plus these rules:

- Pick up only issues labelled `agent-ready`, assign the issue, and move it to In progress.
- Branch from an up-to-date `main` with the naming above, and open a draft pull request early with `Closes #N`.
- Run everything CONTRIBUTING.md asks for before marking the pull request ready, and say in the description what was run.
- Add a `Co-Authored-By:` trailer naming the agent to each commit it writes.
- **Never** push to `main`, approve a pull request, merge one, change repository settings, or commit study data or credentials. A person approves and merges.
- If the issue turns out to be underspecified, stop, ask in the issue, and remove `agent-ready`.

`AGENTS.md` in each repository describes its code for agents; this file describes the process.

## Repository setup

What is configured, so it can be checked or recreated. Settings live in GitHub, not in the repository, so record changes to them here and in `.github/scripts/apply_repo_settings.sh`, which applies them (it needs repository admin and is safe to re-run).

| Setting | Value | Where |
|---|---|---|
| Default branch | `main`, protected by the `main` ruleset | Settings → Rules |
| Ruleset `main` | Pull request required, 1 approval; required checks `lint`, `test`, `linked-issue`, branch up to date; merge commit only; no force-push or deletion; no bypass | Settings → Rules |
| Merge buttons | Merge commit only; delete head branch on merge; suggest updating branches | Settings → General |
| CI | `ci.yml` (`lint`, `test`), `linked-issue.yml` | `.github/workflows/` |
| Board automation | `project-status.yml`, using the `PROJECT_TOKEN` secret | `.github/workflows/`, Settings → Secrets |
| Dependencies | Dependabot alerts and security updates; weekly version updates for pip and GitHub Actions (Ruff excluded: it's bumped deliberately) | `.github/dependabot.yml`, Settings → Code security |
| Secrets | Secret scanning and push protection on | Settings → Code security |
| Templates | Issue forms, pull request template, CODEOWNERS | `.github/` |

`PROJECT_TOKEN` is a fine-grained personal access token owned by `heal-data-stewards`, with **Projects: read and write** (organization) and **Issues** and **Pull requests: read** on both repositories. When it expires, the board automation logs a warning and stops moving items; renew it and update the secret.

`vlmd-app` is private and, on the organization's current plan, can't have rulesets, so these rules are followed there by convention. It uses the same templates and points to this file.

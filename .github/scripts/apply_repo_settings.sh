#!/usr/bin/env bash
# Applies the repository settings described in "Repository setup" in DEVELOPMENT.md.
# Needs repository admin. Safe to re-run: every call sets a value rather than toggling it.
#
#   bash .github/scripts/apply_repo_settings.sh            # everything except the linked-issue required check
#   bash .github/scripts/apply_repo_settings.sh --require-linked-issue
#                                                          # also require linked-issue (run once linked-issue.yml is on main)
set -euo pipefail

REPO=heal-data-stewards/heal-vlmd-AI-pipeline
RULESET_ID=27916927 # the "main" ruleset

checks='{"context": "lint", "integration_id": 15368}, {"context": "test", "integration_id": 15368}'
if [[ "${1:-}" == "--require-linked-issue" ]]; then
  checks="$checks"', {"context": "linked-issue", "integration_id": 15368}'
fi

echo "== Ruleset: pull request + 1 approval, required checks, merge commit only, no force-push or deletion"
# Drops the "update" (Restrict updates) and "creation" rules, which with no bypass actors
# blocked every merge into main.
gh api -X PUT "repos/$REPO/rulesets/$RULESET_ID" --input - --jq '[.rules[].type]' <<EOF
{
  "name": "main",
  "target": "branch",
  "enforcement": "active",
  "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
  "bypass_actors": [],
  "rules": [
    {"type": "deletion"},
    {"type": "non_fast_forward"},
    {"type": "pull_request", "parameters": {
      "required_approving_review_count": 1,
      "dismiss_stale_reviews_on_push": false,
      "require_code_owner_review": false,
      "require_last_push_approval": false,
      "required_review_thread_resolution": false,
      "allowed_merge_methods": ["merge"]}},
    {"type": "required_status_checks", "parameters": {
      "strict_required_status_checks_policy": true,
      "do_not_enforce_on_create": false,
      "required_status_checks": [$checks]}}
  ]
}
EOF

echo "== Merge settings: merge commit only, delete branches on merge, suggest updating branches"
gh api -X PATCH "repos/$REPO" --silent \
  -F allow_merge_commit=true -F allow_squash_merge=false -F allow_rebase_merge=false \
  -F delete_branch_on_merge=true -F allow_update_branch=true

echo "== Security: secret scanning + push protection, Dependabot alerts and security updates"
gh api -X PATCH "repos/$REPO" --silent --input - <<'EOF'
{"security_and_analysis": {
  "secret_scanning": {"status": "enabled"},
  "secret_scanning_push_protection": {"status": "enabled"}}}
EOF
gh api -X PUT "repos/$REPO/vulnerability-alerts" --silent
gh api -X PUT "repos/$REPO/automated-security-fixes" --silent

echo "== Labels: remove the phase labels (the board's Phase field replaces them) and unused defaults; add agent-ready"
for label in phase-1 phase-2 phase-3 phase-4 phase-5 "Phase 0" enhancement invalid question duplicate wontfix repo-app; do
  gh label delete "$label" --repo "$REPO" --yes 2>/dev/null && echo "  deleted $label" || echo "  $label: already gone"
done
gh label create agent-ready --repo "$REPO" --force --color 0E8A16 \
  --description "Specified well enough for an autonomous agent to pick up; see DEVELOPMENT.md"

echo "== Stale branches: five merged ones, and file-splitting (PR #1, closed unmerged in July; its commits stay reachable from the PR)"
for branch in APPL_ID10154341 add-agents-md feat/excel-hdp01011 update_hbcd_yaml update-readme file-splitting; do
  gh api -X DELETE "repos/$REPO/git/refs/heads/$branch" --silent 2>/dev/null && echo "  deleted $branch" || echo "  $branch: already gone"
done

echo
echo "Done. Still to do by hand (see the pull request for #88):"
echo "  - An org owner creates the vlmd-devs team with write access (CODEOWNERS names it)."
echo "  - Create the PROJECT_TOKEN fine-grained PAT and add it as a repository secret."
echo "  - Turn on the board's 'Auto-add to project' workflow for both repositories."

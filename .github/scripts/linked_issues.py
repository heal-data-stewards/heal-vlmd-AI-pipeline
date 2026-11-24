"""Find the issues a pull request closes, and keep them in step on the project board.

Used by two workflows; see "Pull requests" and "The board" in DEVELOPMENT.md.

    python .github/scripts/linked_issues.py check   # linked-issue.yml: fail if the PR closes no issue
    python .github/scripts/linked_issues.py sync    # project-status.yml: move closed issues on the board

A pull request's issues are the union of two sources:
- closing keywords in its description (`Closes #N`), parsed here. This is the only source
  for a stacked pull request, because GitHub links keywords only on pull requests into main.
- the issues GitHub itself links to it (`closingIssuesReferences`): keywords on a pull request
  into main, an issue linked in the Development sidebar, and a branch made with an issue's
  "Create a branch" button.

Standard library only, so the workflows need no install step.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request

# GitHub's closing keywords, followed by #N or owner/repo#N. A keyword inside an HTML
# comment (such as the pull request template's instructions) doesn't count.
CLOSING_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+"
    r"(?:(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+))?#(?P<number>\d+)\b",
    re.IGNORECASE,
)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

# Board statuses in workflow order. The automation only moves an issue forward along this
# list, except that a pull request turned back into a draft returns its issues to In progress.
STATUS_ORDER = ["Backlog", "Ready", "In progress", "Needs review", "In review", "Done"]


def closing_refs(body, default_repo):
    """Return the (owner, repo, number) issues a pull request body closes, in order, without repeats."""
    default_owner, default_name = default_repo.split("/", 1)
    text = HTML_COMMENT_RE.sub("", body or "")
    refs = []
    for m in CLOSING_RE.finditer(text):
        ref = (
            m.group("owner") or default_owner,
            m.group("repo") or default_name,
            int(m.group("number")),
        )
        if ref not in refs:
            refs.append(ref)
    return refs


def target_status(event_name, event):
    """Return (status, may_move_back) for a pull request event, or None to leave the board alone."""
    pr = event["pull_request"]
    if event_name == "pull_request_review":
        # The author replying to a review isn't a review.
        if event["review"]["user"]["login"] == pr["user"]["login"]:
            return None
        return ("In review", False)
    action = event.get("action")
    if action == "closed":
        # A stacked pull request merged into another branch hasn't reached main yet.
        if pr.get("merged") and pr["base"]["ref"] == event["repository"]["default_branch"]:
            return ("Done", False)
        return None
    if action == "converted_to_draft":
        return ("In progress", True)
    if pr.get("draft"):
        return ("In progress", False)
    return ("Needs review", False)


def should_move(current, target, may_move_back):
    """Whether an item at `current` status (None if unset) should be set to `target`."""
    if current == target:
        return False
    if may_move_back:
        return True
    rank = {name: i for i, name in enumerate(STATUS_ORDER)}
    return rank.get(target, -1) > rank.get(current, -1)


# --- GitHub API ---------------------------------------------------------------------------


class GraphQLError(Exception):
    pass


def graphql(token, query, **variables):
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as e:
        raise GraphQLError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}") from e
    if payload.get("errors"):
        raise GraphQLError("; ".join(err.get("message", str(err)) for err in payload["errors"]))
    return payload["data"]


PROJECT_QUERY = """
query($owner: String!, $number: Int!) {
  organization(login: $owner) {
    projectV2(number: $number) {
      id
      field(name: "Status") { ... on ProjectV2SingleSelectField { id options { id name } } }
    }
  }
}"""

ISSUE_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    issue(number: $number) {
      id
      projectItems(first: 50) {
        nodes {
          id
          project { id }
          fieldValueByName(name: "Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
        }
      }
    }
  }
}"""

LINKED_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      closingIssuesReferences(first: 50) {
        nodes { number repository { name owner { login } } }
      }
    }
  }
}"""

ADD_ITEM = """
mutation($project: ID!, $content: ID!) {
  addProjectV2ItemById(input: {projectId: $project, contentId: $content}) { item { id } }
}"""

SET_STATUS = """
mutation($project: ID!, $item: ID!, $field: ID!, $option: String!) {
  updateProjectV2ItemFieldValue(input: {
    projectId: $project, itemId: $item, fieldId: $field, value: {singleSelectOptionId: $option}
  }) { projectV2Item { id } }
}"""


def github_linked_refs(token, repo_full_name, pr_number):
    """The (owner, repo, number) issues GitHub links to a pull request as closing it."""
    owner, repo = repo_full_name.split("/", 1)
    pr = graphql(token, LINKED_QUERY, owner=owner, repo=repo, number=pr_number)
    nodes = pr["repository"]["pullRequest"]["closingIssuesReferences"]["nodes"]
    return [
        (n["repository"]["owner"]["login"], n["repository"]["name"], n["number"]) for n in nodes
    ]


def all_refs(body, repo_full_name, pr_number, token):
    """Closing keywords in the body plus GitHub's own links, in that order, without repeats.

    If GitHub can't be asked (no token, or the call fails), falls back to the body alone.
    """
    refs = closing_refs(body, repo_full_name)
    if not token:
        return refs
    try:
        linked = github_linked_refs(token, repo_full_name, pr_number)
    except GraphQLError as e:
        print(f"::warning::Couldn't read the issues GitHub links to this pull request: {e}")
        return refs
    return refs + [r for r in linked if r not in refs]


def sync_issue(token, project, ref, target, may_move_back):
    owner, repo, number = ref
    label = f"{owner}/{repo}#{number}"
    issue = graphql(token, ISSUE_QUERY, owner=owner, repo=repo, number=number)["repository"]
    issue = issue and issue["issue"]
    if not issue:
        print(f"::warning::{label} is not an issue (or isn't visible to PROJECT_TOKEN); skipped.")
        return
    item = next(
        (n for n in issue["projectItems"]["nodes"] if n["project"]["id"] == project["id"]), None
    )
    if item is None:
        item_id = graphql(token, ADD_ITEM, project=project["id"], content=issue["id"])
        item_id = item_id["addProjectV2ItemById"]["item"]["id"]
        current = None
        print(f"{label}: added to the board.")
    else:
        item_id = item["id"]
        current = (item.get("fieldValueByName") or {}).get("name")
    if not should_move(current, target, may_move_back):
        print(f"{label}: left at {current!r}.")
        return
    field = project["field"]
    option = next((o["id"] for o in field["options"] if o["name"] == target), None)
    if option is None:
        print(f"::warning::The board has no {target!r} status; {label} not moved.")
        return
    graphql(
        token, SET_STATUS, project=project["id"], item=item_id, field=field["id"], option=option
    )
    print(f"{label}: {current!r} -> {target!r}.")


# --- Commands -----------------------------------------------------------------------------


def cmd_check():
    refs = all_refs(
        os.environ.get("PR_BODY", ""),
        os.environ["GITHUB_REPOSITORY"],
        int(os.environ["PR_NUMBER"]),
        os.environ.get("GH_TOKEN"),
    )
    if refs:
        print("Closes " + ", ".join(f"{o}/{r}#{n}" for o, r, n in refs))
        return 0
    print(
        "::error title=No linked issue::This pull request doesn't close an issue. Add "
        "'Closes #123' (or 'Closes heal-data-stewards/vlmd-app#45') to the description, or link "
        "the issue in the Development sidebar and re-run this check. If there is no issue, "
        "create one with the issue forms and make sure it is on the project board. See "
        "'Pull requests' in DEVELOPMENT.md."
    )
    return 1


def cmd_sync():
    event_name = os.environ["GITHUB_EVENT_NAME"]
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as f:
        event = json.load(f)
    decision = target_status(event_name, event)
    if decision is None:
        print("Nothing to move.")
        return 0
    token = os.environ.get("PROJECT_TOKEN")
    if not token:
        # Expected for reviews on pull requests from forks, which get no secrets.
        print("::warning::PROJECT_TOKEN is not set, so the board was not updated.")
        return 0
    pr = event["pull_request"]
    refs = all_refs(pr.get("body"), event["repository"]["full_name"], pr["number"], token)
    if not refs:
        print("This pull request closes no issue; nothing to move.")
        return 0
    target, may_move_back = decision
    try:
        project = graphql(
            token,
            PROJECT_QUERY,
            owner=os.environ["PROJECT_OWNER"],
            number=int(os.environ["PROJECT_NUMBER"]),
        )["organization"]["projectV2"]
        for ref in refs:
            try:
                sync_issue(token, project, ref, target, may_move_back)
            except GraphQLError as e:
                # e.g. "Closes #61" where #61 is a pull request, not an issue.
                print(f"::warning::{'{}/{}#{}'.format(*ref)} not moved: {e}")
    except GraphQLError as e:
        # Board bookkeeping must never block a pull request; an expired token shows up here.
        print(f"::warning::Couldn't update the board: {e}")
    return 0


if __name__ == "__main__":
    commands = {"check": cmd_check, "sync": cmd_sync}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        sys.exit(f"usage: {sys.argv[0]} {{{'|'.join(commands)}}}")
    sys.exit(commands[sys.argv[1]]())

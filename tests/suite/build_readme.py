"""Regenerate the fixture table in README.md from manifest.yaml and failure_modes.yaml.

python tests/suite/build_readme.py           # rewrite README.md
python tests/suite/build_readme.py --check   # exit 1 if README.md is out of date
"""

import sys
from pathlib import Path

import suite_lib

README = suite_lib.SUITE_DIR / "README.md"
BEGIN, END = "<!-- BEGIN GENERATED -->", "<!-- END GENERATED -->"


def _status(case: dict) -> str:
    tier = case.get("tier", "offline")
    if case.get("status") == "xfail":
        return "xfail (whole case)"
    n = len(case.get("xfail") or {})
    parts = [] if tier == "offline" else [f"tier: {tier}"]
    if n:
        parts.append(f"{n} of {len(suite_lib.check_ids(case))} checks xfail")
    return "; ".join(parts) or "passing"


def generate() -> str:
    cases = suite_lib.load_manifest()
    modes = suite_lib.load_failure_modes()
    out = [
        "### Fixtures",
        "",
        "| Fixture | Covers | What it checks | Status |",
        "|---|---|---|---|",
    ]
    for c in cases:
        covers = ", ".join(f"`{m}`" for m in c["covers"])
        desc = " ".join(str(c["description"]).split()).replace("|", "\\|")
        out.append(f"| `{c['id']}/{c['fixture']}` | {covers} | {desc} | {_status(c)} |")

    out += ["", "### Failure modes", "", "| Id | Meaning | Fixtures |", "|---|---|---|"]
    for mid, meaning in modes.items():
        used = [f"`{c['id']}`" for c in cases if mid in c["covers"]]
        out.append(
            f"| `{mid}` | {meaning} | {', '.join(used) if used else '**not covered yet**'} |"
        )

    known = [
        f"- `{c['id']}::{cid}`: {reason}"
        for c in cases
        for cid, reason in (c.get("xfail") or {}).items()
    ] + [f"- `{c['id']}` (whole case): {c['xfail_reason']}" for c in cases if c.get("xfail_reason")]
    out += ["", "### Known failures (xfail)", ""]
    out += known or ["None."]
    return "\n".join(out)


def render(readme_text: str) -> str:
    head, _, rest = readme_text.partition(BEGIN)
    _, _, tail = rest.partition(END)
    return f"{head}{BEGIN}\n{generate()}\n{END}{tail}"


def main() -> int:
    current = README.read_text(encoding="utf-8")
    new = render(current)
    if "--check" in sys.argv:
        if new != current:
            print("tests/suite/README.md is out of date; run: python tests/suite/build_readme.py")
            return 1
        return 0
    README.write_text(new, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

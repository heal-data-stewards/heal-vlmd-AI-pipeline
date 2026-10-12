"""Fetch study metadata from the HEAL Data Platform API.

Retrieves APPL_ID and key study details for a given HDP_ID so the user
can confirm they have the right study before conversion begins.

API:  https://healdata.org/mds/metadata/{hdp_id}
Docs: https://healdata.org/mds/openapi
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

from cli_ui import bold, bold_yellow

HEAL_MDS_BASE = "https://healdata.org/mds/metadata"


def fetch_study_metadata(hdp_id: str, timeout: int = 10, retries: int = 3) -> dict:
    """Fetch raw metadata JSON from the HEAL platform. Raises RuntimeError on failure."""
    import time

    url = f"{HEAL_MDS_BASE}/{hdp_id}"
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise RuntimeError(
                    f"HDP_ID '{hdp_id}' not found on the HEAL platform (HTTP 404). "
                    "Check the ID at https://healdata.org/discovery"
                )
            if e.code in (502, 503, 504) and attempt < retries - 1:
                wait = 2**attempt
                print(
                    f"  HEAL API returned HTTP {e.code}, retrying in {wait}s ...",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(wait)
                last_err = e
                continue
            raise RuntimeError(f"HEAL API returned HTTP {e.code} for '{hdp_id}'.")
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"Could not reach the HEAL platform: {e.reason}. Check your network connection."
            )
    raise RuntimeError(f"HEAL API unavailable after {retries} attempts: {last_err}")


def extract_study_info(metadata: dict) -> dict:
    """Pull the key fields from the raw API response into a flat dict."""
    nih = metadata.get("nih_reporter", {})
    gen3 = metadata.get("gen3_discovery", {})
    sm = gen3.get("study_metadata", {})
    mi = sm.get("minimal_info", {})
    ml = sm.get("metadata_location", {})

    appl_id = nih.get("appl_id") or ml.get("nih_application_id")

    return {
        "appl_id": str(appl_id) if appl_id else None,
        "study_name": mi.get("study_name") or nih.get("project_title", ""),
        "alternative_name": mi.get("alternative_study_name") or "",
        "description": (mi.get("study_description") or "")[:250].strip(),
        "investigators": gen3.get("investigators_name") or [],
        "institution": (
            gen3.get("institutions") or (nih.get("organization") or {}).get("org_name", "")
        ),
        "research_program": gen3.get("research_program") or "",
        "research_focus": gen3.get("research_focus_area") or "",
        "year_awarded": gen3.get("year_awarded") or nih.get("fiscal_year"),
        "nih_reporter_link": ml.get("nih_reporter_link") or "",
    }


def display_study_info(hdp_id: str, info: dict):
    """Print a study summary card to stdout."""
    import textwrap

    div = "─" * 60

    def row(label: str, value: str):
        first, *rest = textwrap.wrap(
            str(value), width=60, initial_indent=f"  {label:<14}", subsequent_indent=" " * 16
        )
        print(first)
        for line in rest:
            print(line)

    print()
    print(bold_yellow(f"  HEAL Platform — {hdp_id}"))
    print(f"  {div}")
    row("Study:", info["study_name"])
    if info["alternative_name"] and info["alternative_name"] != info["study_name"]:
        row("Alt name:", info["alternative_name"])
    row("APPL_ID:", info["appl_id"] or "not found")
    if info["institution"]:
        row("Institution:", str(info["institution"]))
    if info["investigators"]:
        row("PI(s):", ", ".join(info["investigators"][:3]))
    if info["research_program"]:
        row("Program:", info["research_program"])
    if info["research_focus"]:
        row("Focus:", info["research_focus"])
    if info["year_awarded"]:
        row("Awarded:", str(info["year_awarded"]))
    if info["nih_reporter_link"]:
        row("NIH link:", info["nih_reporter_link"])
    if info["description"]:
        print("\n  Description:")
        for line in textwrap.wrap(
            info["description"], width=72, initial_indent="    ", subsequent_indent="    "
        ):
            print(line)
    print(f"  {div}")
    print()


def lookup(hdp_id: str, provided_appl_id: str | None = None) -> tuple[str | None, dict]:
    """
    Fetch and display study info for hdp_id.

    Returns (appl_id, info_dict).
    If provided_appl_id conflicts with the fetched one, prints a warning.
    """
    try:
        raw = fetch_study_metadata(hdp_id)
    except RuntimeError as e:
        print(f"  WARNING: {e}", file=sys.stderr)
        return provided_appl_id, {}

    info = extract_study_info(raw)
    display_study_info(hdp_id, info)

    fetched_appl_id = info.get("appl_id")

    if (
        provided_appl_id
        and provided_appl_id != "unknown"
        and fetched_appl_id
        and str(provided_appl_id) != str(fetched_appl_id)
    ):
        print(
            f"  WARNING: --appl-id {provided_appl_id} does not match the platform "
            f"record ({fetched_appl_id}). Using the provided value.",
            file=sys.stderr,
        )
        return provided_appl_id, info

    return fetched_appl_id or provided_appl_id, info


def confirm_study(no_confirm: bool = False) -> bool:
    """Prompt the user to confirm. Returns True to proceed, False to abort."""
    if no_confirm:
        return True
    try:
        answer = input(f"  {bold('Proceed with this study? [Y/n]:')} ").strip().lower()
        return answer in ("", "y", "yes")
    except (EOFError, KeyboardInterrupt):
        # Non-interactive environment — require explicit --no-confirm
        print(
            "\n  Non-interactive session detected. Pass --no-confirm to skip confirmation.",
            file=sys.stderr,
        )
        return False


def main():
    ap = argparse.ArgumentParser(
        description="Look up a HEAL study by HDP_ID and display its metadata."
    )
    ap.add_argument("hdp_id", help="HEAL Data Platform project ID (e.g. HDP01258)")
    ap.add_argument("--json", action="store_true", help="Output raw extracted info as JSON")
    args = ap.parse_args()

    appl_id, info = lookup(args.hdp_id)

    if args.json:
        print(json.dumps({"appl_id": appl_id, **info}, indent=2))
    else:
        print(f"  APPL_ID: {appl_id}")

    sys.exit(0 if appl_id else 1)


if __name__ == "__main__":
    main()

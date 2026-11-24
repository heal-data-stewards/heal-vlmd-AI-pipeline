"""Resolve NDA structure names to short_names and fetch their definitions,
so a batch of NDAR data dictionaries can be converted without any local
export in hand — only a list of names (display titles or short_names), or
just an NDA collection ID when one is known.

Steps, each independently callable:
  1. resolve_short_names      — match input names against NDA's public catalog
     / resolve_collection      — or, given a collection ID, list exactly the
                                 structures NDA says are shared for it (no
                                 fuzzy matching needed — prefer this when the
                                 collection ID is known)
  2. fetch_structures         — download each short_name's definition CSV from
                                 NDA's public per-structure endpoint
  3. fetch_and_convert        — fetch_structures, then hand the resulting
                                 directory to vlmd_ndar.convert_workbook (no
                                 conversion logic duplicated here)

NDA endpoints used here, all confirmed to work with no authentication:
  - https://nda.nih.gov/api/datadictionary/v2/datastructure
    (full catalog — ignores any collectionId/scope filter, returns all
    ~6700 structures with title + shortName; used for name resolution when
    no collection ID is known)
  - https://nda.nih.gov/api/nda/collection/{collection_id}/data-structures
    (the list backing the collection page's own "Shared Data" tab — exact
    short_name + title + unique_subject_count for just that collection;
    found by reading nda.nih.gov's own Angular bundle, CollectionsApiService
    .getCollectionSharedData — confirmed live against collection 5121/A2CPS)
  - https://nda.nih.gov/api/datadictionary/v2/datastructure/{short_name}/csv
    (one structure's authoritative definition, same 9-column schema
    vlmd_ndar.py already parses)

Usage (standalone):
    python vlmd_ndar_lookup.py resolve "PHQ-9" "Demographics"
    python vlmd_ndar_lookup.py collection 5121
    python vlmd_ndar_lookup.py fetch phq01 demographics01 --dest-dir work/ndar_fetch
    python vlmd_ndar_lookup.py convert phq01 demographics01 \\
        --hdp-id HDP01494 --appl-id 123 --title "My Study" --output-dir output
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

CATALOG_URL = "https://nda.nih.gov/api/datadictionary/v2/datastructure"
COLLECTION_STRUCTURES_URL = "https://nda.nih.gov/api/nda/collection/{collection_id}/data-structures"
STRUCTURE_CSV_URL = "https://nda.nih.gov/api/datadictionary/v2/datastructure/{short_name}/csv"
DEFAULT_CACHE_PATH = Path(__file__).parent / "work" / "ndar_catalog_cache.json"
CACHE_MAX_AGE_SECONDS = 24 * 3600
FUZZY_MATCH_COUNT = 3
FUZZY_MATCH_CUTOFF = 0.75


def _get(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def fetch_catalog(cache_path: Path | None = None, max_age_seconds: int = CACHE_MAX_AGE_SECONDS) -> list[dict]:
    """Return NDA's full data-structure catalog (title + shortName for ~6700
    structures), from a local cache if it's fresh enough, else refetched."""
    cache_path = cache_path or DEFAULT_CACHE_PATH
    if cache_path.exists() and (time.time() - cache_path.stat().st_mtime) < max_age_seconds:
        return json.loads(cache_path.read_text(encoding="utf-8"))

    raw = _get(CATALOG_URL)
    catalog = json.loads(raw)
    if not isinstance(catalog, list) or not catalog:
        raise RuntimeError(f"NDA catalog endpoint returned an unexpected shape: {type(catalog)}")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(catalog), encoding="utf-8")
    return catalog


def fetch_collection_structures(collection_id: str, timeout: int = 20) -> list[dict]:
    """Return the exact list of structures NDA's own "Shared Data" tab shows
    for this collection — each a dict with short_name, title, data_type,
    unique_subject_count. Raises urllib.error.HTTPError (404) for an unknown
    or inaccessible collection ID."""
    url = COLLECTION_STRUCTURES_URL.format(collection_id=collection_id)
    raw = _get(url, timeout=timeout)
    structures = json.loads(raw)
    if not isinstance(structures, list):
        raise RuntimeError(f"NDA collection data-structures endpoint returned an unexpected shape: {type(structures)}")
    return structures


def resolve_collection(collection_id: str) -> list[dict]:
    """Same result shape as resolve_short_names, for a collection ID instead
    of a list of names — every row is already an exact match (status
    "collection"), since NDA's own collection endpoint gives the short_name
    directly. Prefer this over resolve_short_names whenever the collection
    ID is known; no fuzzy matching is involved."""
    structures = fetch_collection_structures(collection_id)
    return [
        {
            "input": s.get("title", s.get("short_name", "")),
            "short_name": s.get("short_name"),
            "title": s.get("title", ""),
            "status": "collection",
            "candidates": [],
            "unique_subject_count": s.get("unique_subject_count"),
        }
        for s in structures
    ]


def _looks_like_short_name(name: str, by_short_name: dict[str, dict]) -> bool:
    return name.strip().lower() in by_short_name


def resolve_short_names(names: list[str], catalog: list[dict] | None = None) -> list[dict]:
    """Match each input name to an NDA short_name.

    Each input is checked, in order:
      1. Already a known short_name (case-insensitive exact) -> status "short_name"
      2. Exact title match (case-insensitive, whitespace-normalized) -> status "exact"
      3. Fuzzy title match -> status "fuzzy", with up to FUZZY_MATCH_COUNT candidates
      4. Nothing close -> status "unmatched"

    Returns one dict per input, in the same order, with keys:
      input, short_name (best guess or None), title, status, candidates
    (candidates is a list of {short_name, title, score} — always present,
    empty when status is "short_name" or "exact").
    """
    catalog = catalog if catalog is not None else fetch_catalog()
    by_short_name = {e["shortName"].strip().lower(): e for e in catalog if e.get("shortName")}
    by_title = {}
    for e in catalog:
        title = (e.get("title") or "").strip().lower()
        if title:
            by_title.setdefault(title, e)
    titles = list(by_title.keys())

    results = []
    for raw_name in names:
        name = raw_name.strip()
        key = name.lower()

        if key in by_short_name:
            entry = by_short_name[key]
            results.append({
                "input": raw_name, "short_name": entry["shortName"], "title": entry.get("title", ""),
                "status": "short_name", "candidates": [],
            })
            continue

        if key in by_title:
            entry = by_title[key]
            results.append({
                "input": raw_name, "short_name": entry["shortName"], "title": entry.get("title", ""),
                "status": "exact", "candidates": [],
            })
            continue

        close = difflib.get_close_matches(key, titles, n=FUZZY_MATCH_COUNT, cutoff=FUZZY_MATCH_CUTOFF)
        if close:
            candidates = [
                {"short_name": by_title[t]["shortName"], "title": by_title[t].get("title", ""),
                 "score": round(difflib.SequenceMatcher(None, key, t).ratio(), 3)}
                for t in close
            ]
            best = candidates[0]
            results.append({
                "input": raw_name, "short_name": best["short_name"], "title": best["title"],
                "status": "fuzzy", "candidates": candidates,
            })
            continue

        results.append({"input": raw_name, "short_name": None, "title": "", "status": "unmatched", "candidates": []})

    return results


def fetch_structure_csv(short_name: str, dest_dir: Path, timeout: int = 20) -> Path:
    """Download one structure's definition CSV to dest_dir/{short_name}_definitions.csv
    (the exact filename shape vlmd_ndar.iter_forms already expects for a directory input)."""
    url = STRUCTURE_CSV_URL.format(short_name=short_name)
    req = urllib.request.Request(url, headers={"Accept": "text/csv"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_path = dest_dir / f"{short_name}_definitions.csv"
    out_path.write_bytes(body)
    return out_path


def fetch_structures(short_names: list[str], dest_dir: Path) -> list[dict]:
    """Fetch each short_name's definition CSV. Returns one result dict per
    short_name: {short_name, success, path|error}. Never raises — a failure
    on one structure doesn't abort the rest of the batch."""
    results = []
    for short_name in short_names:
        try:
            path = fetch_structure_csv(short_name, dest_dir)
            results.append({"short_name": short_name, "success": True, "path": str(path)})
        except urllib.error.HTTPError as e:
            results.append({"short_name": short_name, "success": False,
                             "error": f"HTTP {e.code} — short_name likely doesn't exist on NDA"})
        except Exception as e:
            results.append({"short_name": short_name, "success": False, "error": f"{type(e).__name__}: {e}"})
    return results


def fetch_and_convert(
    short_names: list[str],
    hdp_id: str,
    appl_id: str,
    title: str,
    output_dir: str,
    skip_llm: bool = False,
    model: str = "azure-gpt-4.1-mini",
    dest_dir: str | None = None,
    on_progress: Any = None,
) -> dict:
    """Fetch every short_name's definition, then run the whole batch through
    vlmd_ndar.convert_workbook (extract -> convert -> validate -> fixup ->
    merge, per form) — no conversion logic duplicated here.

    on_progress, if given, is called as on_progress(stage, short_name, detail)
    after each structure's fetch and after each form's conversion, so a
    caller (e.g. an async job wrapper) can report incremental progress
    without this function knowing anything about jobs/polling.

    Returns: {fetch_results, converted, failed} — converted/failed are lists
    of short_name/form names; a short_name that failed to fetch never reaches
    the convert step, so it shows up in fetch_results but not in either list.
    """
    from vlmd_ndar import convert_workbook

    fetch_dir = Path(output_dir) / "ndar_fetch" / (hdp_id or "scratch")
    fetch_results = []
    for short_name in short_names:
        batch = fetch_structures([short_name], fetch_dir)
        result = batch[0]
        fetch_results.append(result)
        if on_progress:
            on_progress("fetch", short_name, result)

    fetched_ok = [r["short_name"] for r in fetch_results if r["success"]]
    if not fetched_ok:
        return {"fetch_results": fetch_results, "converted": [], "failed": []}

    rc = convert_workbook(
        str(fetch_dir), hdp_id, appl_id, title, output_dir, skip_llm, model, dest_dir,
    )
    # convert_workbook doesn't report per-form results to a caller today
    # (only prints them) — re-derive success/failure from what it wrote out.
    from vlmd_ndar import _clean_name  # noqa: E501 — reuse the same name-cleaning rule it uses internally

    converted, failed = [], []
    vlmd_dir = Path(output_dir) / "vlmd"
    for short_name in fetched_ok:
        clean = _clean_name(short_name)
        matches = list(vlmd_dir.glob(f"*{clean}*")) if vlmd_dir.exists() else []
        ok = bool(matches)
        (converted if ok else failed).append(short_name)
        if on_progress:
            on_progress("convert", short_name, {"success": ok})

    return {"fetch_results": fetch_results, "converted": converted, "failed": failed}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_resolve = sub.add_parser("resolve", help="Match names against NDA's public catalog")
    p_resolve.add_argument("names", nargs="+")

    p_collection = sub.add_parser("collection", help="List structures shared for an NDA collection ID")
    p_collection.add_argument("collection_id")

    p_fetch = sub.add_parser("fetch", help="Download one or more structures' definition CSVs")
    p_fetch.add_argument("short_names", nargs="+")
    p_fetch.add_argument("--dest-dir", required=True)

    p_convert = sub.add_parser("convert", help="Fetch and fully convert one or more structures")
    p_convert.add_argument("short_names", nargs="+")
    p_convert.add_argument("--hdp-id", default="")
    p_convert.add_argument("--appl-id", default="unknown")
    p_convert.add_argument("--title", default="HEAL Study Data Dictionary")
    p_convert.add_argument("--output-dir", required=True)
    p_convert.add_argument("--skip-llm", action="store_true")
    p_convert.add_argument("--dest-dir", default=None)

    args = ap.parse_args()

    if args.cmd == "resolve":
        results = resolve_short_names(args.names)
        print(json.dumps(results, indent=2))
        sys.exit(0 if all(r["status"] != "unmatched" for r in results) else 1)

    if args.cmd == "collection":
        results = resolve_collection(args.collection_id)
        print(json.dumps(results, indent=2))
        sys.exit(0)

    if args.cmd == "fetch":
        results = fetch_structures(args.short_names, Path(args.dest_dir))
        print(json.dumps(results, indent=2))
        sys.exit(0 if all(r["success"] for r in results) else 1)

    if args.cmd == "convert":
        result = fetch_and_convert(
            args.short_names, args.hdp_id, args.appl_id, args.title,
            args.output_dir, args.skip_llm, dest_dir=args.dest_dir,
        )
        print(json.dumps(result, indent=2))
        sys.exit(0 if not result["failed"] else 1)


if __name__ == "__main__":
    main()

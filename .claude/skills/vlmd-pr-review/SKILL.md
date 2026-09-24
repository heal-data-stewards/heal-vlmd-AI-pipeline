---
name: vlmd-pr-review
description: Review a pull request in uc-cdis/heal-data-dictionaries that adds a VLMD data-dictionary conversion (input codebook -> generated .vlmd.json/.vlmd.csv). Checks structural validity against the HEAL VLMD JSON/CSV schema and a set of data-quality heuristics (type sensibility, enum-in-description duplication, custom-field duplication, variable-count parity, oversized enums that should be min/max ranges, description quality). Use when the user asks to review/audit a VLMD conversion PR, or check a data dictionary conversion against the HEAL schema.
---

# VLMD PR review

Reviews VLMD conversion output (produced by the heal-vlmd-AI-pipeline or submitted
manually) against:
- the target schemas: `heal_json.json` and `heal_csv.json` from
  `uc-cdis/heal-platform-sdk` (`heal/vlmd/schemas/`)
- a data-quality checklist that schema validation alone can't catch

## When to use

The user asks to review/audit one or more open PRs in `uc-cdis/heal-data-dictionaries`
(or a local VLMD json/csv pair) against the HEAL VLMD schema and conversion-quality
criteria.

## Review criteria this checks

1. Data type makes sense for each variable (`type` vs the actual values / enum).
2. Allowable values / enumerations aren't ALSO spelled out redundantly in `description`.
3. No unnecessary/duplicative information in the `custom` object (e.g. a custom key
   that just repeats `section` or `description`, or reuses a name that's already a
   core VLMD property).
4. Number of variables in the output matches the number of rows in the input file(s).
5. Descriptions make sense (not empty, not just the variable name, not truncated).
6. Enumerations make sense - large enumerations of what's really a continuous/ordinal
   scale (e.g. 20+ individual percentage or count values) are flagged as candidates
   for a `constraints.minimum`/`maximum` range instead of `constraints.enum`.
7. Descriptions capture the essence of the corresponding input row (spot-check
   manually using the printed sample - this part needs a human/LLM read, it's not
   fully mechanizable).

Schema validity (required fields, `additionalProperties: false`, `type` enum,
`constraints.*` shapes) is checked automatically via `jsonschema` against
`heal_json.json`.

## Procedure

1. **Identify the PR and its file groups.**
   ```bash
   gh pr view <PR_NUM> --repo uc-cdis/heal-data-dictionaries --json files -q '.files[].path'
   ```
   Group changed files by `data-dictionaries/<HDP_ID>/...`. Each VLMD output group
   looks like `data-dictionaries/<HDP_ID>/vlmd/<name>/<name>.vlmd.json` (+ `.vlmd.csv`
   + `metadata.yaml`), paired with one or more files under
   `data-dictionaries/<HDP_ID>/input/`.

2. **Fetch PR content without touching the user's working tree.** The local clone
   (usually at `~/code/HEAL/heal-data-dictionaries`) may have uncommitted/detached
   state - never `checkout` the PR branch. Instead fetch it to a throwaway ref and
   read blobs with `git show`:
   ```bash
   cd <path-to-heal-data-dictionaries-clone>
   git fetch origin pull/<PR_NUM>/head:refs/pr<PR_NUM>
   git show refs/pr<PR_NUM>:"<path>" > /tmp/.../<file>
   ```
   (Quote paths with spaces - input filenames sometimes have them.)

3. **Fetch the target schemas** (cache in scratchpad, don't re-fetch every run):
   ```
   https://raw.githubusercontent.com/uc-cdis/heal-platform-sdk/master/heal/vlmd/schemas/heal_json.json
   https://raw.githubusercontent.com/uc-cdis/heal-platform-sdk/master/heal/vlmd/schemas/heal_csv.json
   ```

4. **Run the bundled review script** per input/output pair:
   ```bash
   python3 <skill_dir>/scripts/vlmd_pr_review.py \
     --input <input.csv> --output <output.vlmd.json> --schema <heal_json.json>
   ```
   Requires `jsonschema` (`pip install jsonschema` if missing; the script still runs
   without it but skips formal schema validation). Pass `--name-column` if the input
   CSV's variable-name column isn't literally `name`.

   The script reports, in order: variable-count parity + name-set diff, JSON Schema
   violations, then the quality heuristics (empty/short/name-echoing descriptions,
   enum-in-description duplication split into high-confidence (>=3 labels echoed) vs
   possibly-a-false-positive (2 labels, often just natural question phrasing like
   "Remote or on-site?"), oversized numeric enums, type/enum mismatches, and
   `custom.*` fields that duplicate a named property).

5. **Sanity-check flagged items before reporting them as real issues** - several
   heuristics have known false-positive shapes:
   - `suspiciously_short_description` (<=3 chars) catches legitimate single-word
     checklist items (e.g. MB-CDI vocabulary inventories: "cat", "moo") - not a bug.
   - `enum_values_possibly_duplicated_in_description` (2-label bucket) often fires on
     ordinary yes/no-style question phrasing, not real duplication - only the
     >=3-label bucket is high-confidence.
   - A `custom.*` duplication finding that shows up on nearly every field is a
     systemic pipeline/mapping issue worth calling out once, not per-variable noise.

6. **For criterion 7 (descriptions capture the essence of the input row)**, this
   can't be fully automated - spot-check a handful of fields (the script prints
   field 0/1 structure; pull a few more with a quick `jq`/python snippet) by reading
   the input row's label/question text next to the output `description` and judging
   fidelity.

7. **Report** a structural summary (counts, schema violations) plus the quality
   findings grouped by confidence, referencing concrete variable names/examples -
   not just counts. If a finding is systemic (affects most/all variables), say so
   once instead of listing hundreds of instances.

## Notes

- Input files can be very large (HBCD-style dictionaries run 90k+ variables) -
  the script is built for that scale; don't try to eyeball every row manually.
- If `.vlmd.csv` is also present, the CSV schema (`heal_csv.json`) mirrors the JSON
  one but flattens `constraints.enum`/`enumLabels`/etc. into pipe-delimited strings
  (e.g. `"1=Poor|2=Fair"`) - only check it if the JSON/CSV appear to disagree with
  each other; the JSON is the primary artifact.

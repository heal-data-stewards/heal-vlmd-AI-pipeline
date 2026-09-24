# NDAR Data Dictionary Conversion — Design Notes

Captured from a design discussion on 2026-09-03 that was interrupted before implementation started (pivoted to a vlmd-app ticket). Nothing described here — `vlmd_ndar.py`, the catalog, the `short_name`-list workflow — has been built yet. This is the planning state to pick up from, not a status report on working code.

## Goal

Convert NDAR (NIMH Data Archive / NDA) data dictionaries into VLMD, the same way `vlmd_pdf.py`/`vlmd_excel.py`/`cde.yaml` already handle other source formats in this pipeline.

## The raw NDAR format

NDA's own "Data Structure Definitions" export — confirmed by reading two real, already-converted examples (see below) — is a fixed 9-column schema, one row per field:

```
ElementName, DataType, Size, Required, Condition, ElementDescription, ValueRange, Notes, Aliases
```

It arrives as either:
- Multiple single-structure `*_definitions.xlsx` files (one structure per file), or
- One combined multi-sheet workbook, one sheet per structure, each sheet named `{short_name}_definitions` (confirmed: HDP00104's raw file is a single 26-sheet workbook).

**The hard part** is `DataType` + `ValueRange` + `Notes` together deciding the final VLMD field type and choices — `ValueRange` is polymorphic *per row*:
- `GUID`/`Date`/`Float` → straightforward text/date/number
- `Integer` with `ValueRange` like `0::1440` → a numeric **range** (min/max), not an enum
- `Integer` or `String` where `Notes` (sometimes `ValueRange`, sometimes both) contains `key=label;key=label` → an actual enum — the two sources sometimes disagree/need reconciling when one has more entries than the other
- A handful of recurring value-range strings that don't fit any clean rule (hardcoded `ndar_mappings` override dict in the original notebook, ~5 entries)
- Combined cases like `0::1000;-9` — a range plus a sentinel/missing-value code tacked on — handled ad hoc

Because this is per-row polymorphic, a static declarative format YAML (like `redcap.yaml`) can't branch on it — same reason `vlmd_pdf.py` exists as an imperative pre-processing step rather than a YAML.

## Prior art: two already-converted examples

Both fully converted by hand before this discussion, used as ground truth:

- `~/Documents/HEAL/HEAL_DataDictionaries/OriginalDataDictionaries/HDP00201` (raw NDAR originals) → `heal-data-dictionaries/HDP00201` (VLMD output), notebook: `~/code/HEAL/dd_conversion_notebooks/HDP00201.ipynb`
- `~/Documents/HEAL/HEAL_DataDictionaries/OriginalDataDictionaries/HDP00104_STAR_COD_Collection_4128_Metadata_Data_Dictionary_3.26.25.xlsx` (raw, single 26-sheet workbook) → `heal-data-dictionaries/HDP00104` (VLMD output), notebook: `~/code/HEAL/dd_conversion_notebooks/HDP00104.ipynb`

The notebooks' actual transformation logic (distilled from HDP00201.ipynb):
1. Clean the variable name (lowercase, non-word chars → `_`, collapse repeats)
2. Form name = the filename minus `_definitions`
3. Field Label = `ElementDescription`, with a `??` → `'` fix (recurring NDA export mojibake artifact — same class of encoding issue seen elsewhere in this pipeline)
4. Type/choice derivation per the polymorphism rules above
5. Historically output as a REDCap-shaped intermediate CSV, then run through the existing `redcap.yaml` pipeline

Both studies share several structures in common: `demographics01`, `phq01`, `gad701`, `cssrs01`, `genomics_subject02`, `ndar_subject01` all appear in **both** HDP00104 and HDP00201 — direct evidence NDA structures are standardized and reused constantly across HEAL studies (motivates the catalog idea below).

## Key design decision: skip the REDCap intermediate

The original notebooks went NDAR → REDCap-shaped CSV → VLMD (reusing the mature `redcap.yaml` pipeline). Hinashah's call, reasoned through and agreed on: **go directly NDAR → VLMD instead.**

Why: REDCap's own Field Type vocabulary doesn't map cleanly from NDA's `DataType`, and cramming a numeric range into REDCap's Text Validation Min/Max columns just to have `redcap.yaml`'s `type_override` re-derive it is a lossy round-trip that forces manual fiddling — exactly the "significant manual transformation" hinashah described needing for the original conversions.

The engine already supports the needed generic column mappings (`minimum_column`/`maximum_column`/`value_labels_column`, added by a teammate) — the missing piece is purely the per-row polymorphic decision logic, which has to live in code, not YAML.

## Proposed architecture

```
NDAR *_definitions.xlsx (one or many sheets/files, all same 9-column schema)
        │
        ▼
   vlmd_ndar.py  (new — mirrors vlmd_pdf.py / vlmd_excel.py)
        │  deterministic: name cleaning, form→section, GUID/Date/Float typing,
        │                 clean min::max ranges
        │  LLM-assisted:  ValueRange + Notes → enum vs. range vs. free text
        │                 (the genuinely judgment-heavy part from the notebook)
        ▼
   intermediate CSV — OUR shape: name, label, section, type, choices, minimum, maximum, required, aliases
   (type is already a final VLMD type string; choices already in our standard "value, label | value, label" format)
        │
        ▼
   formats/ndar_intermediate.yaml  (new, thin, purely declarative —
   type_mapping is just identity, minimum_column/maximum_column point straight at those columns)
        │
        ▼
      VLMD
```

**Input granularity — resolved:** build around the **whole workbook** from the start (not one-sheet-at-a-time processing), but keep **output as separate per-form VLMD files** for v1, matching the existing HDP00104/HDP00201 outputs. Reasoning: NDA's own structures share a common subject/visit-identifying spine — `subjectkey`, `src_subject_id`, `interview_date`, `interview_age` appear in **all 26** of HDP00104's forms (confirmed via the notebook's own `value_counts()` output). Treating each form in total isolation throws that connective structure away, and there's a real future use case (longitudinal data, detecting/deduplicating shared cross-form variables) that whole-workbook input costs little to keep open now but would require rework to retrofit later. Actually modeling the longitudinal/shared-variable link is explicitly **out of scope for v1** — flagged as real design work of its own (what counts as "the same variable" across forms? how does VLMD represent a field shared across sections?), not something that should block getting basic NDAR conversion working.

## NDA's public API — verified findings

Tested live against `ndar_subject01`, which has local ground truth to check against.

**Confirmed working, no authentication needed:**
- `https://nda.nih.gov/api/datadictionary/v2/datastructure/{short_name}/csv` — fetches a named structure's authoritative definition. Verified: columns and row content (subjectkey, src_subject_id, interview_date, interview_age, sex, race, all values) are **identical** to the local `ndar_subject01_definitions.xlsx`.
- `https://nda.nih.gov/api/collection/{id}` — general collection metadata (title, PI, grant, team, DSA status). Verified against collection 4128 (= HDP00104).
- Documented API spec exists at `nda.nih.gov/api/datadictionary/docs/swagger-ui/index.html` — not yet explored beyond the one endpoint above.

**Confirmed NOT accessible without auth:** the collection JSON's `dataExpected` field is exactly where a collection's list of structure `short_name`s would live — it comes back as an **empty array** unauthenticated. Tried several plausible endpoint variants (`/dataStructures`, `/dataExpected` as its own path, `?collectionId=` filtering, `/summary`); none returned the real list. Getting the authoritative structure list for an arbitrary collection genuinely seems to require a logged-in NDA session.

One caveat on a discarded claim: an early WebFetch of the public `edit_collection.html?id=4128` page summarized "28 approved data structures" with category names, but since the real API's `dataExpected` comes back empty unauthenticated, that was likely the summarizing model inventing plausible content rather than reading real page data — don't treat that specific number as confirmed.

**Practical implication:** for a *new* NDAR collection without a local export already in hand, someone still needs to supply the list of structure `short_name`s the study uses (either pasted in, or obtained while logged into NDA themselves) — that can't be automated yet. But once you have that list, every structure's authoritative definition can be fetched directly via the API, no manual Excel export required.

## The reuse-catalog idea

Hinashah's proposal, and independently well-motivated by the demographics01/phq01/etc. overlap noted above: keep validated VLMD json/csv files in a catalog, keyed by NDA structure `short_name`. When a new NDAR collection comes in, check the catalog before reconverting anything.

Proposed shape: `ndar-catalog/phq01/phq01.vlmd.json` + `.vlmd.csv`, plus a small provenance file per entry recording which HDP studies use it, when it was converted, and — important — a **reviewed/unreviewed** flag. Only auto-reuse entries a human has actually signed off on; surface unreviewed hits for a quick confirm rather than blind reuse.

Workflow for a new collection (given a `short_name` list):
1. For each `short_name`: check the catalog.
2. **Hit** (reviewed) → reuse the field definitions directly, just re-stamp study-specific metadata (`HDP_ID`, `APPL_ID`, title) — no reconversion.
3. **Miss** (or unreviewed) → fetch the structure from NDA's API, run it through `vlmd_ndar.py`, validate, get a human look, then add to the catalog for next time.

**Flagged, not solved:** NDA structures can get versioned/updated over time. A catalog entry should probably record which version it was converted from, so a future NDA update doesn't silently get masked by a stale cached entry. Noted as a real gap; "detect staleness" logic deliberately deferred rather than built speculatively.

**Where should the catalog live? — OPEN, not decided.** Claude's recommendation was `heal-data-dictionaries` (not `heal-vlmd-AI-pipeline`), reasoning: `heal-vlmd-AI-pipeline` is the general-purpose *conversion tool* (REDCap, HBCD, Stata, CDE, generic-csv, soon NDAR all pass through it) and doesn't hold any converted *content* today — all validated output already lives in `heal-data-dictionaries`. Bolting an NDAR-specific catalog onto the tool repo would break that existing split and make NDAR the one format with special-cased storage baked into the tool itself. The only real cost of putting it in `heal-data-dictionaries` instead is `vlmd_ndar.py` needing a configurable path to that repo's checkout (or a `--catalog-dir` flag) rather than assuming a local directory. Hinashah asked "would it be too much to keep the catalog within this repo [heal-vlmd-AI-pipeline]?" and Claude said not from a size perspective (a few dozen to a couple hundred files is trivial for git) but held the recommendation for consistency reasons — **hinashah's actual decision on this was never given before the conversation moved on.**

## Immediate next step when resuming

Confirm the catalog location, then start on `vlmd_ndar.py` — whole-workbook input, per-form output, common-variable/longitudinal handling explicitly flagged as future work, not blocking v1.

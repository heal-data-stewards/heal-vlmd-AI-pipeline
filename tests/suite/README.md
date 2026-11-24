# Data-dictionary test suite

Excerpts of real and synthetic data dictionaries that exercise the problems an input dictionary can have (wrong or ambiguous format, HTML, empty descriptions, slider labels, offset headers, encodings, ...). Each case runs through the offline stages (extract → detect → convert → lint, no LLM) and is checked against a small set of *properties* (detected format, row and field counts, schema validity, fields flagged for review, labels preserved), not a golden output.

```bash
pip install -r requirements-dev.txt
python -m pytest tests/suite                 # offline cases
python -m pytest tests/suite --run-local     # also cases that read your local source files
python -m pytest tests/suite --run-llm       # also cases that call an LLM (needs credentials)
```

Every assertion is its own test, named `<case>::<check>`, so a case can be partly passing. A check the pipeline can't pass yet is listed under the case's `xfail:` map with a reason and runs as a strict xfail: when someone fixes it the run fails until the entry is deleted, so the list below only ever shrinks.

## Files

| File | Purpose |
|---|---|
| `manifest.yaml` | One entry per fixture: what it covers, what is expected, which checks are known failures |
| `failure_modes.yaml` | The catalog of failure-mode ids that `covers:` refers to |
| `fixtures/<case>/` | The excerpt itself (small; never a whole dictionary) |
| `suite_lib.py`, `test_suite.py`, `conftest.py` | Runner and checks |
| `build_fixtures.py` | Cuts an excerpt out of a local source file and records where it came from |
| `build_readme.py` | Regenerates the tables below from the two YAML files |
| `sources.local.yaml` | **Gitignored.** Local path, hash, sheet and row selection for every extracted fixture |

The tables below are generated: edit `manifest.yaml` or `failure_modes.yaml`, then run `python tests/suite/build_readme.py`. A test fails if the README is out of date.

## Limits of excerpts

An excerpt can't reproduce behavior that depends on the size of the original. Sheet selection in `vlmd_excel.py` picks the sheet with the most cells, which an excerpt changes, so workbook fixtures pin a `sheet:` in the manifest and test what happens to that sheet. Large-input behavior (chunking, cost) isn't covered here.

## Adding a fixture

1. Cut an excerpt that contains the problem, not the first N rows, with `build_fixtures.py extract` (see `--help`). Keep it small. `build_fixtures.py` always replaces emails, phone numbers and URLs, and takes `--redact 'REGEX=>REPLACEMENT'` for people's names; still read the excerpt before committing it. Before committing an excerpt of a real dictionary, confirm that the study's dictionary is cleared for sharing (see CONTRIBUTING.md). Never commit participant data.
2. Add an entry to `manifest.yaml`. Assert properties of the *correct* output, and put anything the pipeline gets wrong today under `xfail:` with the reason.
3. `python tests/suite/build_readme.py`, then `python -m pytest tests/suite`.

<!-- BEGIN GENERATED -->
### Fixtures

| Fixture | Covers | What it checks | Status |
|---|---|---|---|
| `synthetic-redcap-types/input.csv` | `redcap-detect`, `slider`, `calc`, `descriptive`, `html`, `count-parity` | Hand-written REDCap dictionary with one of each field type: text with validation, radio, yesno, checkbox, calc, three slider label shapes, descriptive with HTML, notes and a piped field. | 6 of 10 checks xfail |
| `redcap-slider-minimal/input.csv` | `redcap-detect`, `slider`, `calc`, `short-description`, `count-parity` | A whole small REDCap test project: two sliders (text endpoints with an empty middle label, and three text labels), both with a custom maximum of 6, plus a calc field whose formula contains a comma. | 3 of 9 checks xfail |
| `redcap-html-checkbox/input.csv` | `redcap-detect`, `html`, `calc`, `descriptive`, `count-parity` | REDCap survey dictionary with rich-text HTML in descriptive fields, two checkbox fields and two calc formulas (one multi-line with nested if/sum). | 5 of 8 checks xfail |
| `redcap-slider-formats-a/input.csv` | `redcap-detect`, `slider`, `count-parity` | Nine sliders with two endpoint labels (`None \| Extremely`) and the show-number flag set, next to a few ordinary fields. | 2 of 6 checks xfail |
| `redcap-slider-formats-b/input.csv` | `redcap-detect`, `slider`, `calc`, `count-parity` | Slider labels with numbers embedded (`0 No Pain \| 50 \| 100 Worst Pain`), and a slider with a custom 1-10 range, plus a calc with a comma-containing formula and two checkbox fields. | 4 of 7 checks xfail |
| `redcap-all-types/input.csv` | `redcap-detect`, `slider`, `calc`, `descriptive`, `html`, `short-description`, `count-parity` | Excerpt with every REDCap field type of a large survey project: notes, dropdown, yesno, checkbox, file uploads, three sliders, calc formulas with piped arguments, and descriptive fields holding HTML (one is an empty div). Cells contain line breaks. | 6 of 11 checks xfail |
| `redcap-empty-descriptions/input.csv` | `redcap-detect`, `empty-description`, `short-description`, `calc`, `count-parity` | REDCap fields with an empty label, a one-character label that only repeats a number, and calc fields with very short labels. Missing and unusable descriptions must be flagged for review, never filled in. | 2 of 15 checks xfail |
| `redcap-named-qualtrics/input.xlsx` | `mislabelled-format`, `redcap-detect`, `count-parity` | Workbook from a study that calls it a "Qualtrics data dictionary": the master sheet has the REDCap column layout but `Variable Name` instead of `Variable / Field Name`, and `string` as the field type. The format must be decided by the columns, not the file name. | 2 of 4 checks xfail |
| `redcap-messy-workbook/input.xlsx` | `redcap-detect`, `enum-variants`, `count-parity` | Workbook with a REDCap-derived dictionary that has only some of the REDCap columns (no `Field Type`, four empty trailing columns), choices written as `1: Male \| 2: Female`, plus a comparison sheet, a hidden sheet and a leftover empty sheet. The sheet is pinned because the excerpt changes which sheet is "largest". | 4 of 5 checks xfail |

### Failure modes

| Id | Meaning | Fixtures |
|---|---|---|
| `redcap-detect` | REDCap export recognised as REDCap and converted as such | `synthetic-redcap-types`, `redcap-slider-minimal`, `redcap-html-checkbox`, `redcap-slider-formats-a`, `redcap-slider-formats-b`, `redcap-all-types`, `redcap-empty-descriptions`, `redcap-named-qualtrics`, `redcap-messy-workbook` |
| `ndar-detect` | NDAR data structure recognised and converted with the NDAR rules | **not covered yet** |
| `qualtrics-detect` | Native Qualtrics export recognised as Qualtrics | **not covered yet** |
| `mislabelled-format` | File name or sheet name suggests one format, the columns are another | `redcap-named-qualtrics` |
| `already-heal` | Already a HEAL VLMD file; should pass through unchanged | **not covered yet** |
| `not-a-dictionary` | Not a data dictionary at all; should be rejected cleanly | **not covered yet** |
| `ambiguous-format` | Columns match several formats; confidence should be reported as low | **not covered yet** |
| `generic-fallback` | No known format; falls back to the generic or LLM-proposed mapping | **not covered yet** |
| `pdf` | PDF codebook (text layer, tables split across pages) | **not covered yet** |
| `multi-sheet` | One workbook, many sheets (one per instrument or aim) | **not covered yet** |
| `hidden-sheet` | Hidden, stray or empty sheets next to the real dictionary | **not covered yet** |
| `prose-sheet` | A sheet of notes or prose instead of a table | **not covered yet** |
| `header-offset` | Header row is not row 1 (title rows, blank leading rows) | **not covered yet** |
| `delimiter` | Tab- or semicolon-separated input | **not covered yet** |
| `encoding` | Latin-1, BOM or mojibake | **not covered yet** |
| `multi-file` | One dictionary split over several files | **not covered yet** |
| `duplicate-versions` | Several near-identical versions of the same dictionary | **not covered yet** |
| `transposed` | Variables run across columns instead of down rows | **not covered yet** |
| `truncated-file` | File ends mid-row | **not covered yet** |
| `empty-description` | Descriptions missing; must be flagged, not invented | `redcap-empty-descriptions` |
| `placeholder-description` | TBD, N/A, "see protocol" and similar | **not covered yet** |
| `short-description` | Description is very short or only repeats the name | `redcap-slider-minimal`, `redcap-all-types`, `redcap-empty-descriptions` |
| `html` | HTML tags and entities in labels or descriptions | `synthetic-redcap-types`, `redcap-html-checkbox`, `redcap-all-types` |
| `piped-fields` | REDCap piped fields such as [var] in text | **not covered yet** |
| `enum-in-description` | Value codes written into the description text | **not covered yet** |
| `slider` | REDCap slider; the labels are endpoint text, not value codes | `synthetic-redcap-types`, `redcap-slider-minimal`, `redcap-slider-formats-a`, `redcap-slider-formats-b`, `redcap-all-types` |
| `checkbox` | REDCap checkbox; one field exports as several columns | **not covered yet** |
| `calc` | Calculated field; the formula is not a set of choices | `synthetic-redcap-types`, `redcap-slider-minimal`, `redcap-html-checkbox`, `redcap-slider-formats-b`, `redcap-all-types`, `redcap-empty-descriptions` |
| `descriptive` | Display-only field with no data | `synthetic-redcap-types`, `redcap-html-checkbox`, `redcap-all-types` |
| `yesno` | Implicit 1=Yes / 0=No encoding | **not covered yet** |
| `enum-variants` | Several encodings of value labels (1,Yes|2,No / 1=Yes;2=No / newline) | `redcap-messy-workbook` |
| `oversized-enum` | Long code list that should be a min/max range | **not covered yet** |
| `missing-codes` | Codes like -9, 999, NA mixed in with real values | **not covered yet** |
| `type-mismatch` | Declared type disagrees with the values or the validation column | **not covered yet** |
| `date-format` | Date or time format strings | **not covered yet** |
| `duplicate-names` | Same variable name repeated within or across sheets | **not covered yet** |
| `bad-names` | Names that break the schema name pattern | **not covered yet** |
| `custom-columns` | Extra columns that must be kept under `custom` | **not covered yet** |
| `section-rows` | Section headers mixed in with variable rows | **not covered yet** |
| `count-parity` | Number of fields out matches the number of variables in | `synthetic-redcap-types`, `redcap-slider-minimal`, `redcap-html-checkbox`, `redcap-slider-formats-a`, `redcap-slider-formats-b`, `redcap-all-types`, `redcap-empty-descriptions`, `redcap-named-qualtrics`, `redcap-messy-workbook` |
| `prompt-injection` | Instructions hidden in a description | **not covered yet** |
| `partial-valid` | One bad row must not stop the rest being converted | **not covered yet** |

### Known failures (xfail)

- `synthetic-redcap-types::field:age`: REDCap Text Validation Min/Max are not mapped to constraints.minimum/maximum
- `synthetic-redcap-types::field:pain_vas_text`: slider labels are parsed as choices and become constraints.enum
- `synthetic-redcap-types::field:pain_vas_two`: slider labels are parsed as choices and become constraints.enum
- `synthetic-redcap-types::field:pain_vas_anchor`: slider labels are parsed as choices and become constraints.enum
- `synthetic-redcap-types::field:bmi`: calc formula is parsed as a choice and becomes constraints.enum
- `synthetic-redcap-types::field:intro`: HTML is left in descriptions
- `redcap-slider-minimal::field:slider_eg`: slider labels are parsed as choices and become constraints.enum; the slider range (Text Validation Min/Max, else 0-100) is not recorded
- `redcap-slider-minimal::field:slider_mid_eg`: slider labels are parsed as choices and become constraints.enum; the slider range (Text Validation Min/Max, else 0-100) is not recorded
- `redcap-slider-minimal::field:dps`: calc formula is parsed as pipe-separated choices and lands in constraints.enum (cut at the first comma if it has one)
- `redcap-html-checkbox::rows_in`: detect counts physical lines, so cells with line breaks inflate row_count
- `redcap-html-checkbox::field:patient_factsheet`: HTML is left in the description
- `redcap-html-checkbox::field:question`: HTML is left in the description
- `redcap-html-checkbox::field:exclusion_total`: calc formula is parsed as pipe-separated choices and lands in constraints.enum (cut at the first comma if it has one)
- `redcap-html-checkbox::field:nrs_total`: calc formula is parsed as pipe-separated choices and lands in constraints.enum (cut at the first comma if it has one)
- `redcap-slider-formats-a::field:vas01_blfo`: slider labels are parsed as choices and become constraints.enum; the slider range (Text Validation Min/Max, else 0-100) is not recorded
- `redcap-slider-formats-a::field:vas03_post`: slider labels are parsed as choices and become constraints.enum; the slider range (Text Validation Min/Max, else 0-100) is not recorded
- `redcap-slider-formats-b::rows_in`: detect counts physical lines, so cells with line breaks inflate row_count
- `redcap-slider-formats-b::field:dh_vas`: slider labels are parsed as choices and become constraints.enum; the slider range (Text Validation Min/Max, else 0-100) is not recorded
- `redcap-slider-formats-b::field:ada_smile_feel`: slider labels are parsed as choices and become constraints.enum; the slider range (Text Validation Min/Max, else 0-100) is not recorded
- `redcap-slider-formats-b::field:age`: calc formula is parsed as pipe-separated choices and lands in constraints.enum (cut at the first comma if it has one)
- `redcap-all-types::rows_in`: detect counts physical lines, so cells with line breaks inflate row_count
- `redcap-all-types::field:mri_vas_pre_i`: slider labels are parsed as choices and become constraints.enum; the slider range (Text Validation Min/Max, else 0-100) is not recorded
- `redcap-all-types::field:age`: calc formula is parsed as pipe-separated choices and lands in constraints.enum (cut at the first comma if it has one)
- `redcap-all-types::field:paindur`: calc formula is parsed as pipe-separated choices and lands in constraints.enum (cut at the first comma if it has one)
- `redcap-all-types::field:econsent_instructions_v5`: HTML is left in the description
- `redcap-all-types::field:pipe_consent1_v5`: HTML is left in the description
- `redcap-empty-descriptions::field:phq4_sum`: calc formula is parsed as pipe-separated choices and lands in constraints.enum (cut at the first comma if it has one)
- `redcap-empty-descriptions::field:age`: calc formula is parsed as pipe-separated choices and lands in constraints.enum (cut at the first comma if it has one)
- `redcap-named-qualtrics::fields_out`: detect picks redcap (score 0.82) but convert fails: the name column is `Variable Name`, not `Variable / Field Name`
- `redcap-named-qualtrics::schema_valid`: no fields are produced; see fields_out
- `redcap-messy-workbook::format`: only some REDCap columns are present (no Field Type), the score is below the detection threshold and the format is reported unknown
- `redcap-messy-workbook::fields_out`: not converted because the format is not detected
- `redcap-messy-workbook::schema_valid`: not converted because the format is not detected
- `redcap-messy-workbook::field:SEX`: not converted because the format is not detected
<!-- END GENERATED -->

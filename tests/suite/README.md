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

## Adding a fixture

1. Cut an excerpt that contains the problem, not the first N rows, with `build_fixtures.py extract` (see `--help`). Keep it small. Before committing an excerpt of a real dictionary, confirm that the study's dictionary is cleared for sharing (see CONTRIBUTING.md). Never commit participant data.
2. Add an entry to `manifest.yaml`. Assert properties of the *correct* output, and put anything the pipeline gets wrong today under `xfail:` with the reason.
3. `python tests/suite/build_readme.py`, then `python -m pytest tests/suite`.

<!-- BEGIN GENERATED -->
### Fixtures

| Fixture | Covers | What it checks | Status |
|---|---|---|---|
| `synthetic-redcap-types/input.csv` | `redcap-detect`, `slider`, `checkbox`, `calc`, `descriptive`, `yesno`, `html`, `piped-fields`, `count-parity` | Hand-written REDCap dictionary with one of each field type: text with validation, radio, yesno, checkbox, calc, three slider label shapes, descriptive with HTML, notes and a piped field. | 6 of 10 checks xfail |

### Failure modes

| Id | Meaning | Fixtures |
|---|---|---|
| `redcap-detect` | REDCap export recognised as REDCap and converted as such | `synthetic-redcap-types` |
| `ndar-detect` | NDAR data structure recognised and converted with the NDAR rules | **not covered yet** |
| `qualtrics-detect` | Native Qualtrics export recognised as Qualtrics | **not covered yet** |
| `mislabelled-format` | File name or sheet name suggests one format, the columns are another | **not covered yet** |
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
| `empty-description` | Descriptions missing; must be flagged, not invented | **not covered yet** |
| `placeholder-description` | TBD, N/A, "see protocol" and similar | **not covered yet** |
| `short-description` | Description is very short or only repeats the name | **not covered yet** |
| `html` | HTML tags and entities in labels or descriptions | `synthetic-redcap-types` |
| `piped-fields` | REDCap piped fields such as [var] in text | `synthetic-redcap-types` |
| `enum-in-description` | Value codes written into the description text | **not covered yet** |
| `slider` | REDCap slider; the labels are endpoint text, not value codes | `synthetic-redcap-types` |
| `checkbox` | REDCap checkbox; one field exports as several columns | `synthetic-redcap-types` |
| `calc` | Calculated field; the formula is not a set of choices | `synthetic-redcap-types` |
| `descriptive` | Display-only field with no data | `synthetic-redcap-types` |
| `yesno` | Implicit 1=Yes / 0=No encoding | `synthetic-redcap-types` |
| `enum-variants` | Several encodings of value labels (1,Yes|2,No / 1=Yes;2=No / newline) | **not covered yet** |
| `oversized-enum` | Long code list that should be a min/max range | **not covered yet** |
| `missing-codes` | Codes like -9, 999, NA mixed in with real values | **not covered yet** |
| `type-mismatch` | Declared type disagrees with the values or the validation column | **not covered yet** |
| `date-format` | Date or time format strings | **not covered yet** |
| `duplicate-names` | Same variable name repeated within or across sheets | **not covered yet** |
| `bad-names` | Names that break the schema name pattern | **not covered yet** |
| `custom-columns` | Extra columns that must be kept under `custom` | **not covered yet** |
| `section-rows` | Section headers mixed in with variable rows | **not covered yet** |
| `count-parity` | Number of fields out matches the number of variables in | `synthetic-redcap-types` |
| `prompt-injection` | Instructions hidden in a description | **not covered yet** |
| `partial-valid` | One bad row must not stop the rest being converted | **not covered yet** |

### Known failures (xfail)

- `synthetic-redcap-types::field:age`: REDCap Text Validation Min/Max are not mapped to constraints.minimum/maximum
- `synthetic-redcap-types::field:pain_vas_text`: slider labels are parsed as choices and become constraints.enum
- `synthetic-redcap-types::field:pain_vas_two`: slider labels are parsed as choices and become constraints.enum
- `synthetic-redcap-types::field:pain_vas_anchor`: slider labels are parsed as choices and become constraints.enum
- `synthetic-redcap-types::field:bmi`: calc formula is parsed as a choice and becomes constraints.enum
- `synthetic-redcap-types::field:intro`: HTML is left in descriptions
<!-- END GENERATED -->

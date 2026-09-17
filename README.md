# VLMD Conversion Pipeline

Converts data dictionaries from any format to the [HEAL VLMD schema](https://github.com/uc-cdis/heal-platform-sdk/blob/master/heal/vlmd/schemas/heal_json.json) (Variable Level Metadata Dictionary, JSON v0.3.2).

Part of the HEAL VLMD AI tooling suite — Phase 1 (V0, 2026).

---

## What this does

Research studies submit data dictionaries in many formats: HBCD-specific CSVs, REDCap exports, Stata `.dta` files, plain spreadsheets. The HEAL Data Platform requires all of these to be expressed in a common schema — VLMD — before ingestion. Converting by hand is time-consuming and error-prone at scale.

This pipeline automates that conversion:

1. **Looks up** the study on the HEAL platform to confirm APPL_ID, title, and PI before any work starts
2. **Detects** the input format automatically (or asks you if it can't tell)
3. **Converts** all variables to VLMD fields using a declarative mapping spec
4. **Validates** the output against the HEAL JSON schema
5. **Fixes** any remaining issues using an LLM (Azure AI Foundry or Anthropic)
6. **Writes** valid VLMD JSON + CSV + `metadata.yaml`, ready for platform ingestion

The mapping rules for each known format live in small YAML files. Adding support for a new format means writing a YAML file, not Python code.

---

## Quick start

```bash
git clone <repo-url>
cd heal-vlmd-AI-pipeline
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run the bundled examples (no credentials required)
python examples/run_examples.py
```

---

## Pipeline overview

```
Input file (any format)
        │
        ▼
┌───────────────────┐
│  vlmd_lookup.py   │  Fetches study info from healdata.org/mds/metadata/{hdp_id}
│                   │  → displays title, APPL_ID, PI, institution
│                   │  → prompts user to confirm before continuing
└─────────┬─────────┘
          │  confirmed APPL_ID + title
          ▼
┌───────────────────┐
│  vlmd_detect.py   │  Rule-based scoring + LLM fallback
│                   │  → identifies format, or proposes a mapping for unknown files
└─────────┬─────────┘
          │  format YAML path  (e.g. formats/hbcd.yaml)
          ▼
┌───────────────────┐
│  vlmd_convert.py  │  Deterministic field mapper — no LLM, no API calls
│                   │  → 96K rows in ~5 seconds
└─────────┬─────────┘
          │  work/vlmd_converted.json
          ▼
┌───────────────────┐
│  vlmd_lint.py     │  Schema validation via healdata_utils
│                   │  → valid=True → skip to merge
│                   │  → errors found → proceed to fixup
└─────────┬─────────┘
          │  (only if errors)
          ▼
┌───────────────────┐
│  vlmd_fixup.py    │  LLM fixes flagged fields only (chunked, checkpointed)
│                   │  → reads prompt from format YAML
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│  vlmd_merge.py    │  Merges fixes, validates final document, writes VLMD JSON + CSV + metadata.yaml
│                   │  → valid + --dest-dir → copy to {dest-dir}/{appl_id}/{hdp_id}/vlmd/
│                   │  → invalid → write to output/ for inspection, exit 2 (no copy)
└───────────────────┘
```

**Key design principles:**
- The HEAL platform is queried first so the user confirms the right study before any conversion work starts. `--appl-id` and `--title` are auto-populated from the API response.
- The LLM is only ever called on rows that fail validation — typically a handful out of tens of thousands.
- The final VLMD document is validated against the HEAL schema before being copied to the destination. Validation failure writes files locally for inspection but blocks the copy.

---

## Requirements

- **Python 3.11+**
- **Network access** to `healdata.org` for the study lookup step (can be bypassed with `--appl-id` and `--title` if offline)
- **LLM credentials** only if validation finds errors that need fixing (Azure AI Foundry primary, Anthropic alternative)

---

## Setup

### 1. Clone and create a virtual environment

```bash
git clone <repo-url>
cd heal-vlmd-AI-pipeline

python -m venv .venv
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

Or with `uv`:

```bash
uv pip install -r requirements.txt
```

`requirements.txt` includes: `pandas`, `healdata_utils`, `openai`, `anthropic`, `python-dotenv`, `tiktoken`, `pyyaml`, `ftfy`.

For Stata (`.dta`) file support, also install:

```bash
pip install pyreadstat
```

### 3. Configure credentials

Copy the template and fill in your keys:

```bash
cp .env.template .env
```

```bash
# .env

# Azure AI Foundry — primary LLM backend for HEAL
AZURE_OPENAI_API_KEY=<your-key>
AZURE_OPENAI_ENDPOINT=<your-openai-endpoint>
AZURE_OPENAI_API_VERSION=2024-12-01-preview

# Anthropic — optional alternative
ANTHROPIC_API_KEY=sk-ant-<your-key>
```

**Finding your Azure endpoint:** Azure Portal → your AI Foundry project → Settings → API keys & endpoint.

Credentials are only needed for the LLM fixup step. Format detection and conversion run without them.

---

## Command-line usage

All commands assume your virtual environment is active and you are in the `heal-vlmd-AI-pipeline/` directory.

---

### Full pipeline (one command)

```bash
python run_pipeline.py \
  --input  "/path/to/data_dictionary.csv" \
  --hdp-id HDP01258 \
  --study-label HBCD_DataDictionary
```

Providing `--hdp-id` is enough to get started. The pipeline fetches APPL_ID, study title, PI, and institution from the HEAL platform, displays a confirmation card, and waits for your approval before proceeding.

Output is written to a nested directory matching the `heal-data-dictionaries` repo convention:
```
output/HDP01258/
  input/HBCD_datadictionary.csv       ← copy of the original input
  vlmd/HDP01258_HBCD_datadictionary/
    HDP01258_HBCD_datadictionary.vlmd.json
    HDP01258_HBCD_datadictionary.vlmd.csv
    metadata.yaml
work/HDP01258/
  vlmd_converted.json
  vlmd_lint_report.json               ← converter flags (fields needing LLM attention)
  vlmd_validation_report.json         ← schema validation results
  vlmd_llm_cleanup.json               ← per-field LLM reasoning log (what changed and why)
  vlmd_llm_fixes.json
  vlmd_llm_fixes.checkpoint.json
```

**Flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--input FILE` | required | Path to input file (CSV or .dta) |
| `--hdp-id ID` | _(empty)_ | HEAL Data Platform project ID — triggers platform lookup |
| `--appl-id ID` | auto-fetched | Override the APPL_ID if the platform lookup is wrong |
| `--title TEXT` | auto-fetched | Override the study title |
| `--name TEXT` | derived from input filename | Output file stem prefix. Final name: `{hdp-id}_{name}`. Example: `--name HBCD_datadictionary` → `HDP01258_HBCD_datadictionary.vlmd.json` |
| `--format YAML` | auto-detect | Skip format detection, use this format YAML |
| `--model KEY` | `azure-gpt-4.1-mini` | LLM model key (see Models section) |
| `--output-dir DIR` | `output/{hdp-id}/` | Base output directory |
| `--dest-dir DIR` | _(none)_ | Root of destination repository; files copied to `{dest-dir}/{hdp-id}/vlmd/{stem}/` and `{dest-dir}/{hdp-id}/input/` after validation |
| `--yes` / `-y` | off | Skip study confirmation prompt (for scripted/bot use) |
| `--skip-llm` | off | Skip LLM fixup even if validation or converter flags errors |
| `--no-detect` | off | Skip format detection (requires `--format`) |
| `--description-review-decisions PATH` | _(none)_ | Decisions file for short/placeholder descriptions (see below) |

**Examples:**

```bash
# Standard run — APPL_ID and title fetched automatically
python run_pipeline.py \
  --input "/path/to/HBCD_datadictionary.csv" \
  --hdp-id HDP01258 \
  --name HBCD_datadictionary

# Known format, skip detection
python run_pipeline.py \
  --input  "/path/to/HBCD_datadictionary.csv" \
  --hdp-id HDP01258 \
  --format formats/hbcd.yaml \
  --study-label HBCD_DataDictionary

# Copy validated output to destination repository
python run_pipeline.py \
  --input "/path/to/REDCap_DataDictionary.csv" \
  --hdp-id HDP01193 \
  --name SCOPE_DataDictionary \
  --dest-dir /path/to/heal-data-dictionaries/data-dictionaries

# Non-interactive / scripted — skip confirmation
python run_pipeline.py --input file.csv --hdp-id HDP01258 --yes

# No network access — provide APPL_ID and title manually
python run_pipeline.py \
  --input file.csv \
  --appl-id 10381046 \
  --title "My Study"

# Fastest — no LLM, deterministic only
python run_pipeline.py --input file.csv --hdp-id HDP01258 --skip-llm
```

---

### Encoding fixes: local, automatic, no LLM

Mojibake (example: double-encoded UTF-8/Latin-1, e.g. `CafÃ©` → `Café`) and smart-quote/dash
artifacts in `description`, `title`, and `enumLabels` are fixed automatically by
[`ftfy`](https://pypi.org/project/ftfy/) during conversion.
Every fix is logged: `vlmd_convert.py` writes `work/{hdp-id}/vlmd_encoding_fixes.json`
(each entry has `field`, `key`, `before`, `after`) and prints a one-line summary count
so nothing changes silently.

If a character was already unrecoverably lost before the file reached this pipeline
(example: an actual replacement character, `�`, that `ftfy` can't repair), it's left as-is and
flagged with a new `encoding_corruption` issue — that one *does* go through the normal
LLM fixup step (with instructions not to guess at the lost character, just write a
clean description from context), since real content is missing and needs to be
supplied, not just cleaned up.

---

### Short/placeholder descriptions: LLM triage proposes, a human approves

The converter flags any description that's a known placeholder (`"n/a"`, `"none"`, `"todo"`, ...) or that's suspiciously 
short (word-count threshold set by `DESCRIPTION_REVIEW_MAX_WORDS` in `vlmd_convert.py`). Whether a flagged description 
is actually broken needs context a heuristic can't see: `"Header"` or `"Net ID#"` can be a perfectly legitimate short 
label, and a word that looks like an English placeholder can be a correctly-translated word in another language the 
study uses (`"todo"` = "all" in a Spanish-language instrument table, not a to-do marker). So instead of guessing, or 
asking a human to judge bare short strings out of context, an LLM triage pass classifies each candidate into one of three 
buckets using its source-row context:

- `send_to_llm` — genuinely empty-in-spirit, truncated, or an unfilled placeholder; no real content to preserve
- `leave_as_is` — short but already meaningful given context
- `flag_for_human` — real source content that's just terse/technical jargon (e.g. `"relResdiff1"`); the model won't guess at what it means, a human decides


When it's done:

1. `work/{hdp-id}/vlmd_description_review.json` is written — one entry per flagged field with `name`, `description`, 
`section`, `decision`, and `justification`. Entries are ordered `flag_for_human` first (these need the most attention), 
then `send_to_llm`, then `leave_as_is`. `flag_for_human` entries have `"decision": ""`
2. The pipeline prints an "ACTION REQUIRED" block with the counts and the exact next steps, and exits with code `3`.
3. Fill in every blank `"decision"` with `send_to_llm` or `leave_as_is`; edit any pre-filled suggestion you disagree with.
4. Re-run with `--description-review-decisions work/{hdp-id}/vlmd_description_review.json`. Fields marked `send_to_llm` 
go through the normal fixup step; `leave_as_is` fields are dropped from that issue and left untouched (other issues on 
the same field, e.g. `missing_type`, still get fixed).

`--yes` (scripted/bot use) skips triage and the stop-and-wait entirely: any undecided short description defaults 
to `leave_as_is` so automation never blocks or spends LLM calls on triage no one will review. Fields with a genuinely 
empty description (`missing_description`) are never gated — there's nothing ambiguous about an empty string, so those 
always go straight to the LLM.

Triage suggestions are not infallible — treat the file as a draft, not a verdict. The same field, given the same context, 
can come back with a different suggestion on separate runs (LLM non-determinism), and a plausible-sounding justification 
can still be wrong (e.g. a justification arguing a word should be left as-is while the `decision` field says `send_to_llm`, 
or vice versa). 

---

### Step-by-step (individual scripts)

#### 0. Look up a study on the HEAL platform

```bash
# Display study info card
python vlmd_lookup.py HDP01258

# Output as JSON (for scripting)
python vlmd_lookup.py HDP01258 --json
```

`--json` prints a flat dict with `appl_id`, `study_name`, `investigators`, `institution`, etc. Exit 0 if APPL_ID was found, 1 if not.

#### 1. Detect format

```bash
python vlmd_detect.py \
  --input "/path/to/file.csv" \
  --output-json work/detection.json

# Rule-based only (no API call, no credentials needed)
python vlmd_detect.py --input file.csv --no-llm
```

Exit codes: `0` = known format detected, `1` = unknown (needs interview), `2` = error.

The `--output-json` file contains:
```json
{
  "format_name": "hbcd",
  "confidence": 1.0,
  "method": "rule_based",
  "format_yaml_path": "formats/hbcd.yaml",
  "row_count": 96227,
  "column_count": 25,
  "columns": ["name", "description", "..."],
  "proposed_mapping": null
}
```

When `format_name` is null and `proposed_mapping` is set, the file is unknown — see the interview flow below.

#### 2. Save a custom format mapping (unknown files)

When `run_pipeline.py` hits an unrecognized format, it writes the LLM's proposed
mapping to `work/{stem}/vlmd_proposed_mapping.json` and prints a ready-to-run
`vlmd_interview.py save` command in its `ACTION REQUIRED` block — copy/paste it
as-is. After a user confirms the mapping through conversation (editing the JSON
file directly if it needs correcting), run that command, e.g.:

```bash
python vlmd_interview.py save \
  --applid      "12345678" \
  --hdp-id      "HDP01234" \
  --source-file "my_dict.csv" \
  --columns     "VarName,QuestionText,DataType,Choices,Category" \
  --mapping-json "$(cat work/HDP01234/vlmd_proposed_mapping.json)"
```

Reading `--mapping-json` from a file this way (rather than inlining it) avoids
shell-quoting breakage — a single apostrophe in a column label (e.g.
`"Participant's Age"`) would otherwise break an inline `'...'` JSON string. The
mapping file itself looks like:

```json
{
  "name_column": "VarName",
  "description_column": "QuestionText",
  "type_mapping": {
    "source_column": "DataType",
    "lookup": {"text": "string", "numeric": "number", "date": "date"}
  },
  "section": {"primary_column": "Category"},
  "levels": {
    "source_column": "Choices",
    "format": "pipe_separated",
    "pair_separator": ",",
    "choice_separator": "|"
  },
  "minimum_column": null,
  "maximum_column": null,
  "value_labels_column": null,
  "custom_columns": [],
  "capture_unmapped_as_custom": true
}
```

This writes `formats/12345678.yaml`. Future files from this study are auto-detected.

#### 3. Convert

```bash
python vlmd_convert.py \
  --input  "/path/to/file.csv" \
  --format formats/hbcd.yaml \
  --output-fields work/vlmd_converted.json \
  --output-lint   work/vlmd_lint_report.json
```

#### 4. Validate

```bash
python vlmd_lint.py \
  --fields work/vlmd_converted.json \
  --report work/vlmd_validation_report.json \
  --title  "My Study"
# Exit 0 = valid, 2 = schema errors found
```

#### 5. LLM fixup (if validation fails)

```bash
python vlmd_fixup.py \
  --lint       work/vlmd_lint_report.json \
  --output     work/vlmd_llm_fixes.json \
  --checkpoint work/vlmd_llm_fixes.checkpoint.json \
  --format     formats/hbcd.yaml \
  --model      azure-gpt-4.1-mini
```

Re-running after a partial failure resumes from the last checkpoint automatically.

#### 6. Merge and write output

```bash
python vlmd_merge.py \
  --converted   work/vlmd_converted.json \
  --fixes       work/vlmd_llm_fixes.json \
  --output-dir  output/ \
  --appl-id     10381046 \
  --hdp-id      HDP01258 \
  --title       "HBCD Study Data Dictionary" \
  --study-label HBCD_DataDictionary \
  --format-name hbcd \
  --validate
```

---

## Format system

### How formats are defined

Each format is a YAML file in `formats/`. The file describes which columns map to which VLMD properties, how to parse categorical levels, and which columns the detector should look for.

```
formats/
  hbcd.yaml         HBCD study data dictionary (25-column CSV)
  redcap.yaml       REDCap data dictionary export
  stata.yaml        Stata .dta files (via pyreadstat)
  generic-csv.yaml  Fallback: auto-detects common column names
  {applid}.yaml     Custom: saved by the interview flow for unknown formats
```

### Format YAML anatomy

```yaml
format_name: myformat
description: Human-readable description

input_reader: csv          # csv (default) or stata

# Column mapping — string for exact name, list to try candidates in order,
# or {combine: [...], separator: ...} to join multiple columns into one value
name_column: "Variable Name"
description_column: "Description"
# description_column:
#   combine: [Description, Header]
#   separator: " | "        # default: " | "
title_column: null         # omit if no source column

type_mapping:
  source_column: "Data Type"
  lookup:
    text:    string
    numeric: number
    date:    date

enum_ordered:
  source_column: "Scale Type"
  trigger_values: [ordinal]   # set enumOrdered=true when column = one of these

section:
  primary_column: "Instrument"
  fallback_column: "Domain"   # used if primary is empty

levels:
  source_column: "Response Options"
  format: pipe_separated       # json_array | pipe_separated | auto_detect
  pair_separator: ","
  choice_separator: "|"

related_concepts:
  - url_column:   "Documentation URL"
    source_name:  "MyStudy"
    title_column: "Instrument"
    require_http: true

custom_columns: [Notes, Source, InstrumentID]
capture_unmapped_as_custom: false   # true → everything not mapped goes to custom{}

fixup_prompt: prompts/generic_fixup_prompt.md
system_invariants_prompt: prompts/system_invariants_vlmd.md

detection:
  signature_columns:
    strong:     [unique_col_1, unique_col_2]   # weighted 2x in scoring
    supporting: [common_col_1, common_col_2]   # weighted 1x
  confidence_threshold: 0.6
  file_extensions: []   # [.dta] for Stata — extension alone is definitive
```

### Adding a new format

1. Create `formats/{format_name}.yaml` following the anatomy above.
2. Add a format-specific fixup prompt in `prompts/` if needed (or reuse `generic_fixup_prompt.md`).
3. Verify detection scoring: `python vlmd_detect.py --input sample.csv --no-llm`

No Python changes required for standard column patterns.

### Supported `levels.format` values

| Value | Input looks like | Notes |
|-------|-----------------|-------|
| `json_array` | `[{"value":"1","label":"Yes"}]` | Used by HBCD |
| `pipe_separated` | `1, Yes \| 2, No \| 3, Maybe` | Used by REDCap |
| `comma_separated` | `Yes,No,Maybe` | Values only, no labels |
| `stata_value_labels` | _(from pyreadstat metadata)_ | Handled automatically |
| `auto_detect` | _(unknown)_ | Tries json_array → pipe_separated → comma |

### Available LLM models

| Key | Provider | Model ID | Notes |
|-----|----------|----------|-------|
| `azure-gpt-4.1-mini` | Azure AI Foundry | `gpt-4.1-mini` | Default — fast, high quota |
| `azure-gpt-5.4-mini` | Azure AI Foundry | `gpt-5.4-mini` | |
| `azure-gpt-5.4` | Azure AI Foundry | `gpt-5.4` | |
| `azure-gpt-5.5` | Azure AI Foundry | `gpt-5.5` | Most capable Azure option |
| `azure-gpt-chat-latest` | Azure AI Foundry | `gpt-chat-latest` | |
| `azure-deepseek-v4-pro` | Azure AI Foundry | `DeepSeek-V4-Pro` | |
| `claude-haiku` | Anthropic | `claude-haiku-4-5-20251001` | |
| `claude-sonnet` | Anthropic | `claude-sonnet-4-6` | |

Azure AI Foundry is the primary backend for HEAL. Pass `--model <key>` to override.

---

## Unknown format: the interview flow

When detection fails (no known format matches and LLM can't confidently identify one), the pipeline:

1. Calls the LLM with column names and 5 sample rows
2. Proposes a column-to-VLMD mapping
3. Exits with code `1` and writes `work/vlmd_detection.json` with `proposed_mapping`

A bot or developer then:
- Presents the proposed mapping to the user
- Collects corrections through conversation
- Runs `vlmd_interview.py save` with the confirmed mapping

The result is `formats/{applid}.yaml`. Next time a file from this study arrives, it scores at the top of detection automatically — the YAML's `detection.signature_columns` is set to the study's known column names.

---

## Examples

Three synthetic sample dictionaries are included in `examples/` to demonstrate each supported format without real study data or API credentials.

| File | Format | Columns | What it covers |
|------|--------|---------|----------------|
| `examples/hbcd_sample.csv` | HBCD | 25 (full spec) | Continuous, categorical, ordinal, date types; JSON-array levels; branching logic; `url_table` → `relatedConcepts` |
| `examples/redcap_sample.csv` | REDCap | 18 (standard export) | text, radio, checkbox, yesno, slider, dropdown, date field types; pipe-separated choices |
| `examples/generic_sample.csv` | generic-csv | 5 | Common column names (`variable_name`, `label`, `data_type`, `options`, `domain`) auto-matched by candidate lists |

**Run all three:**

```bash
python examples/run_examples.py
```

Each example runs `--skip-llm --no-detect` (format explicitly specified, no API calls) and validates the output. Exit 0 = all pass.

**Run one manually:**

```bash
python run_pipeline.py \
  --input  examples/hbcd_sample.csv \
  --format formats/hbcd.yaml \
  --appl-id example_hbcd \
  --title  "HBCD Sample" \
  --skip-llm --no-detect
```

---

## Bot / API integration

The pipeline is designed from the start to be driven by a conversational interface. All interaction points produce structured JSON, and all state lives in files — making it straightforward to wrap in an MCP server, Streamlit app, or REST API.

### Integration points

| Script | What a bot calls it for | Structured output |
|--------|------------------------|-------------------|
| `vlmd_lookup.py --json` | Fetch study info and APPL_ID before asking the user anything | stdout JSON |
| `vlmd_detect.py --output-json` | Identify the file format | `work/vlmd_detection.json` |
| `vlmd_interview.py save` | Save a user-confirmed mapping after conversation | `formats/{applid}.yaml` |
| `run_pipeline.py` | Run the full conversion once study and format are confirmed | Files in `output/` |

### Conversation state machine

```
User provides HDP_ID + file
        │
        ▼
vlmd_lookup.py
        │  "Study: HBCD / APPL_ID: 10381046 / PI: Smyser. Proceed?"
        │  user confirms ──────────────────────────────────────────────┐
        │                                                              │
        ▼                                                              │
vlmd_detect.py                                                         │
        │                                                              │
        ├── HIGH CONFIDENCE ──→ "This looks like HBCD format."        │
        │                              │ yes → run_pipeline.py ────────┘
        │
        ├── AMBIGUOUS ────────→ "Could be HBCD or REDCap. Which?"
        │                              │ user picks → run_pipeline.py
        │
        └── UNKNOWN ──────────→ "Here's what I found: [proposed mapping]
                                 Does this look right?"
                                       │ user corrects
                                       ▼
                               vlmd_interview.py save
                                       │
                                       ▼
                               run_pipeline.py --format formats/{applid}.yaml
```

### Future MCP server

An MCP server would expose these tools, letting any MCP-compatible client (Claude Desktop, a web chatbot, etc.) drive the pipeline conversationally:

```python
# mcp_server.py (stub — not yet implemented)

@mcp.tool()
def lookup_study(hdp_id: str) -> dict:
    """Fetch study info from the HEAL platform. Returns appl_id, study_name, investigators."""
    ...

@mcp.tool()
def detect_format(file_path: str, model: str = "azure-gpt-4.1-mini") -> dict:
    """Detect the format of a data dictionary file."""
    ...

@mcp.tool()
def save_format_mapping(applid: str, mapping: dict, hdp_id: str = "") -> dict:
    """Save a confirmed column mapping as formats/{applid}.yaml for future reuse."""
    ...

@mcp.tool()
def run_conversion(file_path: str, format_yaml: str, appl_id: str,
                   hdp_id: str = "", dest_dir: str = "") -> dict:
    """Run the full conversion pipeline. Returns output file paths and validation status."""
    ...
```

### Calling the pipeline from Python

Because the pipeline is pure Python, the cleanest integration is a direct import:

```python
import sys
sys.path.insert(0, "/path/to/heal-vlmd-AI-pipeline")

from vlmd_lookup import lookup, confirm_study
from vlmd_detect import detect
from run_pipeline import run
from pathlib import Path

# Look up study and get APPL_ID
appl_id, study_info = lookup("HDP01258")

# Detect format
detection = detect("/path/to/dict.csv", formats_dir="formats")

# Run full conversion
exit_code = run(
    input_file="/path/to/dict.csv",
    format_yaml=detection["format_yaml_path"],
    appl_id=appl_id,
    hdp_id="HDP01258",
    title=study_info["study_name"],
    study_label="MyStudy",
    model="azure-gpt-4.1-mini",
    skip_llm=False,
    no_detect=True,
    yes=True,                          # skip interactive confirmation
    output_dir=Path("output"),
    dest_dir=Path("/path/to/CleanedDataDictionaries"),
)
```

---

## File structure

```
heal-vlmd-AI-pipeline/
│
├── README.md                    This file
├── requirements.txt             Python dependencies
├── .env.template                Copy to .env and fill in API keys
├── .gitignore
│
├── run_pipeline.py              One-command orchestrator (pure Python)
│
├── vlmd_lookup.py               HEAL platform lookup — fetches APPL_ID, title, PI
├── vlmd_detect.py               Format detection (rule-based + LLM fallback)
├── vlmd_convert.py              YAML-driven field mapper (no LLM)
├── vlmd_interview.py            Save unknown format as formats/{applid}.yaml
├── vlmd_lint.py                 Schema validation via healdata_utils
├── vlmd_fixup.py                LLM fixup for flagged rows (chunked, checkpointed)
├── vlmd_description_triage.py   LLM triage for short/placeholder descriptions (proposes, doesn't decide)
├── vlmd_merge.py                Write VLMD JSON + CSV + metadata.yaml; gates copy on validation
├── llm_client.py                Azure / Anthropic model registry
├── cli_ui.py                    Shared terminal styling: progress bars, ACTION REQUIRED blocks
│
├── formats/                     Format mapping specs (one YAML per format)
│   ├── hbcd.yaml
│   ├── redcap.yaml
│   ├── stata.yaml
│   ├── generic-csv.yaml
│   └── {applid}.yaml            Created by vlmd_interview.py for unknown formats
│
├── prompts/                     LLM prompt files (referenced from format YAMLs)
│   ├── system_invariants_vlmd.md
│   ├── detect_format_prompt.md
│   ├── hbcd_fixup_prompt.md
│   ├── redcap_fixup_prompt.md
│   ├── generic_fixup_prompt.md
│   └── description_triage_prompt.md
│
├── examples/                    Sample inputs and end-to-end demo
│   ├── hbcd_sample.csv          Synthetic HBCD dictionary (8 rows, 25 columns)
│   ├── redcap_sample.csv        Synthetic REDCap export (8 rows, standard columns)
│   ├── generic_sample.csv       Generic CSV with common column names (8 rows)
│   ├── run_examples.py          Runs all three formats, validates output, no credentials needed
│   └── output/                  Created by run_examples.py (gitignored)
│
├── work/                        Intermediate files — gitignored
│   ├── vlmd_converted.json
│   ├── vlmd_lint_report.json
│   ├── vlmd_validation_report.json
│   ├── vlmd_llm_fixes.json
│   └── vlmd_llm_fixes.checkpoint.json
│
└── output/                      Final VLMD files — gitignored
    ├── {applid}_{label}.vlmd.json
    ├── {applid}_{label}.vlmd.csv
    └── metadata.yaml
```

---

## VLMD schema reference

The target schema is [HEAL VLMD JSON v0.3.2](https://github.com/uc-cdis/heal-platform-sdk/blob/master/heal/vlmd/schemas/heal_json.json).

**Every field must have:**
- `name` — variable identifier as it appears in the data
- `description` — plain-English description of what the variable measures

**Commonly populated:**
- `type` — `number`, `integer`, `string`, `boolean`, `date`, `datetime`, `time`
- `section` — the instrument, form, or domain this variable belongs to
- `constraints.enum` + `enumLabels` — allowed values and their labels for categorical variables
- `enumOrdered` — `true` for ordinal scales
- `relatedConcepts` — links to instrument documentation
- `custom` — any format-specific metadata that doesn't fit the schema

Validation is performed by `healdata_utils.validate_vlmd_json()` (installed via `healdata-utils` in `requirements.txt`).

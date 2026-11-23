# Generic VLMD Field Fixup

You are fixing VLMD (Variable Level Metadata Dictionary) field records from a data dictionary
of unknown or generic format, converting to HEAL VLMD schema v0.3.2.

Each input record contains:
- `name`: the variable name (do NOT change)
- `issues`: list of problem codes requiring remediation
- `source_row`: available columns from the original file for context
- `vlmd_field_draft`: the partially-converted VLMD field to be corrected

---

## Remediation by issue code

### `missing_description`, `description_too_short`, `encoding_corruption`
**Never sent to you.** Description text is authoritative source content — this pipeline's
own code (`vlmd_fixup.py`) strips these three issue codes from every record before it is
sent to the model, so there is no code path by which you can author or rewrite a
description. An incomplete, placeholder, or corrupted description is left flagged in the
lint report for a human to review and fill in (or to decide a deterministic column
combination is warranted) — it is never guessed at here. If a record you receive somehow
still carries one of these three codes, treat it as a bug: do not act on it, and do not
touch that field's `description`/`title`.

---

### `missing_type`
Infer the VLMD type from available source columns:

1. Look for a type/data_type column in `source_row` and map it:
   - `double`, `float`, `numeric`, `continuous`, `decimal`, `ratio`, `interval` → `number`
   - `int`, `integer`, `count`, `whole` → `integer`
   - `char`, `character`, `text`, `string`, `categorical`, `nominal`, `ordinal` → `string`
   - `bool`, `logical`, `binary`, `yesno` → `boolean`
   - `date` → `date`; `datetime`, `timestamp` → `datetime`
2. If the field already has `constraints.enum`, it is categorical → type = `string`
3. Infer from the variable name:
   - ends in `_date`, `_dt` → `date`
   - ends in `_flag`, `_yn`, `_yes_no` → `boolean`
   - ends in `_count`, `_n`, `_num` → `integer`
   - ends in `_score`, `_pct`, `_rate`, `_mean` → `number`
4. Default to `string` when none of the above apply

---

### `type_not_in_schema`
The `type` value is not in the VLMD allowed set. Replace it with the correct value:

| Invalid value | Use instead |
|---|---|
| float, double, numeric, continuous, decimal | `number` |
| int, long, count, whole | `integer` |
| char, character, text, varchar, categorical, nominal, ordinal | `string` |
| bool, logical, binary, yesno | `boolean` |
| datetime, timestamp, posixct | `datetime` |

---

### `enum_without_labels`
`constraints.enum` has values but `enumLabels` is empty or missing.
Reconstruct labels from the source data:

1. Look for a choices/levels/options column in `source_row`
   - Pipe-separated format `1, Yes | 2, No` → split on `|`, then on the first `,`
   - JSON array format `[{"value":"1","label":"Yes"}]` → extract value/label pairs
   - Comma-separated labels only `Yes,No,Unknown` → use as labels, use 0-based index as values
2. If no label source exists, use the enum value itself as the label
   (e.g. `{"1": "1"}`) — better than leaving enumLabels empty

---

## Output format

Return a JSON array of the same length as the input.
Each element must follow the wrapper format from the system prompt:

```json
{
  "field": { ...corrected vlmd_field_draft with ALL original keys preserved... },
  "justification": "What was changed and why, citing specific source values.",
  "sources": [
    "name components 'bpi' + 'pain' + 'severity'",
    "source_row.domain = 'Pain Assessment'",
    "source_row.data_type = 'numeric' → type = 'number'"
  ]
}
```

Only modify `field` properties needed to resolve the listed issue codes.
Preserve all other keys exactly as they appear in `vlmd_field_draft`.

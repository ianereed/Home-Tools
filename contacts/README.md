# contacts

A toolbox of one-shot Python scripts for maintaining `antora_contacts.xlsx`, a working Excel workbook of work-context (Antora) vendor contacts. Each script does a specific Excel-mutation operation: download, populate, fill missing names, add directory rows, add comm columns, etc.

The workbook is the source of truth. The scripts are operations performed against it, NOT a pipeline.

## What it is

```
  antora_contacts.xlsx  (the source of truth)
        ▲
        │  (each script reads, mutates, writes)
        │
  ┌─────┴──────────────────────────────────────────┐
  add_*.py        download_*.py     populate_*.py  update_*.py
  (additions)     (fetch latest)    (fill gaps)    (mutations)
```

## Audience

You — work context (Antora vendors). Single-user.

## Status

Functional. Each script is a one-shot you run when you need to apply that specific transformation.

## How to use

There's no entry point. Pick the script that matches what you want to do:

| Script | Purpose |
|--------|---------|
| `download_contacts_xlsx.py` | Pull the latest workbook from Drive/SharePoint |
| `populate_contacts_sheet.py` | Initial population from a source list |
| `add_directory.py` | Append a directory section |
| `add_new_vendors.py` | Append vendor rows |
| `add_comm_columns.py` | Add communication-cadence columns |
| `update_contacts_columns.py` | Apply schema changes to existing rows |
| `fill_missing_names.py` | Use Gmail to find names for unknown contacts |
| `gmail_all_vendors.py` | Cross-reference Gmail conversations with vendor list |

## Naming convention

`<verb>_<noun>.py`. Verb = action (`add`, `download`, `fill`, `populate`, `update`). Noun = the thing it touches.

## Setup

```bash
cd contacts
python -m venv .venv
source .venv/bin/activate
pip install openpyxl requests google-api-python-client  # see imports per script
```

No pinned `requirements.txt` (deliberate — these scripts share an ad-hoc venv).

## Future

Likely consolidate into a single CLI (`contacts add comm-columns`, `contacts populate sheet`, etc.) once the operations stabilize. Currently adding scripts as needs arise; premature consolidation would slow iteration.

## Out of scope

- Generic CRM functionality
- Apollo integration (separate tooling)
- Multi-user contact management

## Reference

`antora_contacts.xlsx` is the canonical store; scripts are stateless against it.

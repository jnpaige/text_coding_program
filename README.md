# text_coding_program

Browser-based interactive text coding against structured codebooks, inspired by [McPherron's E4/Enterer Trois](https://www.oldstoneage.com/osa/tech/e4/). This program takes in the same kind of information that Site_coder ingests: a text corpus processed with pdf_ocr, a page mapping that highlights pages that need to be coded with site_form_segmenter, and codebook entries extracted from a natural language codebook using codebook_tools. The goal of this program is to help coders more effectively code text, without juggling multiple files, whily minimizing coder fatigue, and helping to reduce entry error. 


## Pipeline context

This tool sits at the end of a multi-repo pipeline. Each upstream repo produces output that feeds the next:

```
pdf_ocr ──────► site_form_segmenter ──────► text_coding_program
                                      └───► site_coder (LLM batch)
codebook_tools ───────────────────────────►
```
See [pdf_ocr](https://github.com/jnpaige/pdf_ocr) for more detail about this workflow. 


## What this program does

A single-page web app served locally that presents:

- **Left panel** — PDF page viewer with page navigation (arrow keys or buttons). Pages are scoped to the current segment using the segment map: any key in a segment ending in `_pages` (besides the base `pages` list itself) is treated as a named page group and unioned to build the scoped page list — `form_pages`/`narrative_pages`/`nrhp_pages` for site forms, or whatever dynamically-named `<section_type>_pages` keys a report-structural segmenter pass produced (see `segment_reports_pass0.py`). No group present → falls back to showing the segment's full `pages` list.
- **Right panel** — Full codebook entry text for the current trait, plus data entry controls (radio buttons for binary, text input for numeric, radio buttons for categorical, checkboxes for list) and a justification field.
- **Unit selector** — Dropdown showing each `(trinomial, segment)` pair from the segment map (e.g., "16VN1000 — Initial Survey (1997)").
- **Progress sidebar** — Shows which traits have been coded for the current segment, with click-to-jump navigation.

Workflow: the coding unit is one segment within one file (a segment from `segments.json` — for site forms this is typically a site investigation, but the tool doesn't assume that; site_form_segmenter's own output schema is generic, and a segment could equally be a report structural section or a narrowed per-trinomial pass). For each unit, you code all traits, then advance to the next segment.

### Keyboard shortcuts

| Key | Action |
|---|---|
| **Enter** | Save current trait and advance to next |
| **Esc** | Skip to next trait without saving |
| **← →** | Previous / next PDF page |

## Setup

### 1. Install uv

```powershell
# Windows — install uv once per machine
winget install astral-sh.uv
```

```bash
# Mac/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Install dependencies

```powershell
uv sync
```

`uv.lock` is committed to the repo, so every machine gets identical package versions.

If you prefer to activate the venv manually:

```powershell
# Windows
.venv\Scripts\activate
python server.py
```

```bash
# Mac/Linux
source .venv/bin/activate
python server.py
```

## Usage

### 1. Configure paths

Edit `config.yaml` (or copy `config_test.yaml` as a starting point):

```yaml
pdf_dir: 'path/to/pdf_ocr/output'             # <trinomial>/ subdirs with _ocr.pdf files
codebook_dir: 'path/to/codebook_tools/output'  # per-trait JSON files
segments_dir: 'path/to/segmenter/run/model'    # <trinomial>.segments.json files
```

### 2. Start the server

```powershell
uv run python server.py --coder jpaige
```

If `--coder` is omitted, you'll be prompted for your coder ID. The server auto-finds an open port if the configured one is busy.

```powershell
# Use a different config
uv run python server.py --config config_test.yaml --coder jpaige
```

### 3. Open browser

Navigate to the URL printed in the terminal. It carries a session token:

```
http://127.0.0.1:8090/?token=<random>
```

The token is generated fresh on every start and authorizes your browser for
that run — visiting `http://127.0.0.1:8090` without it returns 401 with
instructions. Don't bookmark the tokenized URL; start from the terminal (or the
exe) each session. See "Local access control" below for why it's there.

The packaged exe opens this URL for you, so coders never have to copy a token.

## Output

Output follows the same run directory convention as site_coder, with the coder ID in place of the model name:

```
runs/
  YYYYMMDD_HHMM_gitsha/
    jpaige/                         coder ID folder
      16VN1000.coded.json
      16VN1001.coded.json
      ...
```

Each `.coded.json` holds human-coding metadata plus one entry per segment coded:

```json
{
  "trinomial": "16VN1000",
  "segments": [
    {
      "file_trinomial": "16VN1000",
      "segment_label": "Initial Survey",
      "segment_year": 1997,
      "traits": [
        {
          "trait_key": "chipped_stone_chipped_stone",
          "trait_value": true,
          "confidence": 1.0,
          "justification": "Lithic debitage listed in artifact tally",
          "evidence_pages": [0, 1]
        }
      ]
    }
  ],
  "coder_id": "jpaige",
  "first_saved": "2026-06-20T...",
  "last_saved": "2026-06-20T..."
}
```

Saving is a merge keyed on `segment_label`, not a full overwrite — coding one segment of a trinomial doesn't discard previously-saved traits for another segment of the same trinomial.

Sessions are resumable — existing `.coded.json` files are loaded when you revisit a trinomial.

## Project provenance

Each project's `project.json` (in `projects/<slug>/`) records a `provenance` block, captured once at project-creation time and never re-read afterward:

```json
"provenance": {
  "captured_at": "2026-08-11T...",
  "note": "Frozen at project creation — not re-read; may not reflect the source directories' current contents if they were later modified or moved.",
  "codebook_summary": { "codebook_name": "Site_Form", "codebook_version": "v1.1", "code_ids": [...] },
  "run_metadata": { "run_id": "...", "mode": "text", "text_model": "qwen2.5:14b", ... },
  "config_snapshot": { "mode": "text", "temperature": 0.2, ... }
}
```

`run_metadata`/`config_snapshot` come from the segmenter run's own `run_metadata.json` and its config-file snapshot (checked in `segments_dir` itself, then its parent, to cover both the current flat run-folder layout and the older per-model-subfolder one) — only present if the run actually wrote one; older runs predate this and simply won't have it. `config_snapshot` has path-shaped keys (`input_dir`, `codebook_file`, etc.) stripped out, since those describe where the run's *original* creator found its inputs, not where `segments_dir`/`codebook_dir` point to now — this is a historical record, never something the program re-resolves paths from. The live `pdf_dir`/`codebook_dir`/`segments_dir` fields elsewhere in `project.json` are what the program actually operates on; this block exists purely for audit/provenance.

## Input/output contract with site_coder

Verified directly against `site_coder`'s source (`coder.py`, `batch_coder.py`, `lib/reporter.py`) on 2026-08-11. Its raw `.coded.json` was never byte-identical to this tool's — it keeps the segmenter's own `label`/`year`/`pages` fields unprefixed and stores trait values as a flat `codes: {trait_key: value}` dict rather than a `traits: [...]` list of `{trait_key, trait_value, confidence, justification, evidence_pages}` objects. What *is* consistent: the top-level array key is `segments` in both tools, and site_coder's own per-run CSV export (`lib/reporter.py`'s `build_coded_csv`, written alongside every run) already uses `segment_label` as its column name — the same name this tool now uses. (site_coder has one internal inconsistency of its own: a separate, secondary batch-merge script, `merge_results.py`, still uses `investigation_label` instead of matching `reporter.py` — pre-existing, unrelated to this session's changes, not fixed here.)

| | text_coding_program | site_coder |
|---|---|---|
| **Input: PDFs** | `pdf_dir` — renders `_ocr.pdf` for viewing | `md_input_dir` — reads `text_docling.txt` for LLM prompts |
| **Input: segments** | `segments_dir` — scopes pages per segment | `segments_dir` — scopes text per segment |
| **Input: codebook** | `codebook_dir` — displays entries for human coder | `codebook_dir` — injects entries into LLM prompts |
| **Output** | `<coder_id>/<tri>.coded.json` | `<model_slug>/<tri>.coded.json` |
| **Output schema** | `{trinomial, segments: [{segment_label, segment_year, traits: [...]}]}` | `{trinomial, segments: [{label, year, codes: {...}}]}` — same `segments` array, different trait representation |
| **Coding unit** | one segment (from segment map) | one segment (from segment map) |

The trait representations differ (list-of-objects-with-justification/confidence vs. flat value dict) — that gap predates today's changes and isn't new. An IRR comparison pipeline (Cohen's kappa, Krippendorff's alpha) needs to normalize both into a common shape; site_coder does this per-run via `build_coded_csv`. On this side, **project export** now writes the equivalent flat table (`coded_data.csv`, keyed on `trinomial` + `segment_key` + `trait_key`) — see below.

## Exporting results

"Export…" on any project card in the picker writes a folder you can copy to
another computer and read:

```
<project>_export_<timestamp>/
  coded_data.csv       one row per coded trait — the file to open in Excel/R/pandas
  project_export.json  the same data structured rather than flattened
  coded/               the original per-trinomial .coded.json files, unmodified
  codebook/            per-trait JSON, so trait definitions travel with the results
  project.json         project metadata and the source paths this export came from
  manifest.json        counts, contents, SHA-256 of the CSV and JSON
  README.txt           what the folder is, written for whoever receives it
```

Nothing needs to be installed on the receiving machine — open `coded_data.csv`.



## Distributing to a team

The program can be packaged as a standalone `.exe` bundle that requires no Python, uv, or terminal knowledge. Users double-click the exe and a browser opens.


### Building the bundle

```powershell
uv run --extra build python build.py --build
```

Pass `--extra build` on the build command itself rather than relying on an
earlier `uv sync --extra build`. `uv run` resyncs the environment to the
project's *default* dependency set every time it runs, so a plain
`uv run python server.py` in between prunes PyInstaller back out of `.venv` and
the next build dies with `No module named PyInstaller`. `build.py` recovers from
this on its own — it re-invokes PyInstaller through `uv run --extra build` when
it isn't importable — but passing the extra up front skips the round trip.

This produces `dist/text_coding_program/` (~80 MB) containing the exe, Python runtime, and all dependencies, plus a `Text Coding Program.lnk` shortcut next to it in `dist/`. The build script pops up a message box telling you where the shortcut landed so you can move it (Desktop, taskbar, Start menu, etc.) — no manual shortcut-creation step needed.

It also writes `READ_ME_FIRST.txt` into the bundle and prints the exe's SHA-256.
Both exist for the same reason: an unsigned tool from a colleague trips a
SmartScreen prompt, and a coder who was warned in advance — and given a hash
they can check — is making an informed decision rather than a reflexive one.

The build is deliberately shaped to stay off Defender's heuristics; `build.py`'s
module docstring explains each choice (onedir rather than onefile, UPX off, a
real version resource). If you change the packaging, keep those three.

### For end users

1. Unzip `text_coding_program.zip` to any location
2. Double-click `text_coding_program.exe` — expect a SmartScreen prompt the
   first time if the build is unsigned ("More info" → "Run anyway")
3. A browser opens to the project picker — create a new project or resume an existing one
4. Close the console window to stop the server



### Local access control

The server holds a browser-reachable window onto the coder's filesystem: it
renders PDFs, writes coded output, and exposes a native folder-picker dialog.
Binding to `127.0.0.1` keeps it off the network but is not a boundary by itself —
every process running as that user can reach it, and any web page the coder
opens can make their browser send it requests.

Three checks in `server.py`'s
`_local_guard()` close that gap:

| Check | Blocks |
| --- | --- |
| Per-run session token (HttpOnly, `SameSite=Strict` cookie; `X-Session-Token` also accepted) | Other local processes and users driving the server |
| Host header allow-list | DNS rebinding — a remote page pointing a hostname it controls at loopback |
| Origin check | Cross-site requests from any page the coder has open |

Also: `--host` refuses non-loopback addresses unless you pass
`--allow-non-loopback`, since serving this on a LAN exposes the documents being
coded; the save endpoint accepts only trinomials the loaded project actually
discovered, so a crafted request can't steer the write out of the project
folder; and FastAPI's `/docs` and `/openapi.json` are disabled.

One known limitation: cookies are not port-scoped, so a *different* local server
on another port could be sent the session cookie if the browser navigates to it.
That requires an attacker already running code as the coder, which is outside
what a single-user local tool can defend against.

## Dependencies

- [FastAPI](https://fastapi.tiangolo.com/) — web framework
- [uvicorn](https://www.uvicorn.org/) — ASGI server
- [PyMuPDF](https://pymupdf.readthedocs.io/) — PDF page rendering
- [openpyxl](https://openpyxl.readthedocs.io/) — xlsx output
- [PyYAML](https://pyyaml.org/) — config parsing
- [PyInstaller](https://pyinstaller.org/) — packaging (build dependency only)

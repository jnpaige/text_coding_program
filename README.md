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
- **Right panel** — Full codebook entry text for the current trait, plus data entry controls (radio buttons for binary, text input for numeric, radio buttons for categorical) and a justification field.
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

Navigate to the URL printed in the terminal (default `http://127.0.0.1:8090`).

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

The trait representations differ (list-of-objects-with-justification/confidence vs. flat value dict) — that gap predates today's changes and isn't new. An IRR comparison pipeline (Cohen's kappa, Krippendorff's alpha) needs to normalize both into a common shape; site_coder does this per-run via `build_coded_csv`. No equivalent flattening script exists yet on the text_coding_program side.

## Distributing to a team

The program can be packaged as a standalone `.exe` bundle that requires no Python, uv, or terminal knowledge. Users double-click the exe and a browser opens.

**Strategy: build once per machine that needs it, don't hand-copy a bundle built somewhere else.** The bundle *can* be relocated after building (see "Is the dist folder portable?" below), but rebuilding locally is the path that's actually been exercised — every machine that's set this up so far (Cabanerso, Monfrague) ran `build.py --build` itself rather than receiving a copied `dist/` folder from another machine. Prefer that unless there's a specific reason to hand off a pre-built bundle instead.

### Building the bundle

```powershell
# Install the build dependency (one time)
uv sync --extra build

# Build the distributable folder
uv run python build.py --build
```

This produces `dist/text_coding_program/` (~70 MB) containing the exe, Python runtime, and all dependencies, plus a `Text Coding Program.lnk` shortcut next to it in `dist/`. The build script pops up a message box telling you where the shortcut landed so you can move it (Desktop, taskbar, Start menu, etc.) — no manual shortcut-creation step needed.

### Is the dist folder portable?

**Yes, the `dist/text_coding_program/` folder itself** — copy or move it to any location, on this machine or another, and `text_coding_program.exe` still works. It locates `static/`, `projects/`, etc. relative to its own current location at runtime (`Path(sys.executable).parent` in `build.py`'s `launch()`), not a path baked in at build time. Zip it, unzip it elsewhere, done — matches the existing "self-contained, zip it and distribute" note above.

**No, the shortcut is not portable on its own.** `Text Coding Program.lnk` has an absolute path to the exe's location *at build time* baked into it (`build.py`'s `_create_shortcut`). Two consequences:

- Moving just the shortcut (e.g., dragging it to the Desktop) is fine — it still points back at the same `dist/text_coding_program/` folder, wherever that is.
- Moving or renaming the `dist/text_coding_program/` folder *after* the shortcut was created breaks it — the shortcut keeps pointing at the old path. Rebuild (regenerates both the folder and a shortcut matching its new location) rather than trying to hand-fix the `.lnk`.

### For end users

1. Unzip `text_coding_program.zip` to any location
2. Double-click `text_coding_program.exe`
3. A browser opens to the project picker — create a new project or resume an existing one
4. Close the console window to stop the server

Projects are saved in a `projects/` folder next to the exe and persist across sessions. No installation required.

## Dependencies

- [FastAPI](https://fastapi.tiangolo.com/) — web framework
- [uvicorn](https://www.uvicorn.org/) — ASGI server
- [PyMuPDF](https://pymupdf.readthedocs.io/) — PDF page rendering
- [openpyxl](https://openpyxl.readthedocs.io/) — xlsx output
- [PyYAML](https://pyyaml.org/) — config parsing
- [PyInstaller](https://pyinstaller.org/) — packaging (build dependency only)

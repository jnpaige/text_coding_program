# text_coding_program

Browser-based interactive text coding against structured codebooks, inspired by [McPherron's E4/Enterer](https://www.oldstoneage.com/osa/tech/e4/). Designed as a companion to `site_coder` — uses the same inputs (codebook trait JSONs from `codebook_tools`, segment maps from `site_form_segmenter`) and produces the same output JSON format, so human-coded and LLM-coded results can be compared directly for inter-rater reliability analysis.

## What it does

A single-page web app served locally that presents:

- **Left panel** — PDF page viewer with page navigation (arrow keys or buttons). When a segment map is loaded, only the relevant investigation pages are shown.
- **Right panel** — Codebook entry text for the current trait, plus data entry controls (radio buttons for binary, text input for numeric/categorical) and a justification field.
- **Investigation tabs** — When segments are loaded, tabs across the top let you switch between investigations within a site form.
- **Progress sidebar** — Shows which traits have been coded for the current investigation, with click-to-jump navigation.
- **Top bar** — Site selector, trait selector, overall progress badge.

Workflow: select a site → see its pages → code each trait → Enter saves and advances to the next trait → after all traits, advances to the next investigation → after all investigations, advances to the next site.

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

Edit `config.yaml`:

```yaml
pdf_dir: 'path/to/pdf_ocr/output'          # contains <trinomial>/ subdirs with PDFs
codebook_dir: 'path/to/codebook_tools/output'  # per-trait JSON files
segments_dir: 'path/to/segmenter/run/model'    # optional — segments.json per site
```

### 2. Start the server

```powershell
uv run python server.py
```

You'll be prompted for your coder ID. Then open `http://127.0.0.1:8080` in your browser.

```powershell
# Or provide coder ID on the command line
uv run python server.py --coder jpaige

# Use a different config
uv run python server.py --config config_test.yaml
```

### 3. Code

- **Arrow keys** ← → flip PDF pages
- **Enter** saves the current trait and advances
- **Esc** skips to the next trait without saving
- Click any trait in the progress sidebar to jump to it

## Output

```
runs/
  YYYYMMDD_HHMM_gitsha/
    jpaige/                         coder ID folder (like model folder in site_coder)
      16VN1000.coded.json
      16VN1001.coded.json
      ...
      all_coded.{json,csv,xlsx}     aggregate (not yet implemented — run aggregation separately)
```

Each `.coded.json` matches site_coder's format with added human-coding metadata:

```json
{
  "trinomial": "16VN1000",
  "investigations": [
    {
      "file_trinomial": "16VN1000",
      "investigation_label": "Initial Survey",
      "investigation_year": 1997,
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

## Relationship to site_coder

This tool is anchored to what `site_coder` expects as input:

| Input | Source | Shared with site_coder |
|---|---|---|
| PDFs | `pdf_ocr` output | Same `pdf_dir` |
| Codebook traits | `codebook_tools` output | Same `codebook_dir` |
| Segment maps | `site_form_segmenter` output | Same `segments_dir` |

Output `.coded.json` files use the same schema so human and LLM results can be loaded into the same IRR comparison pipeline.

## Dependencies

- [FastAPI](https://fastapi.tiangolo.com/) — web framework
- [uvicorn](https://www.uvicorn.org/) — ASGI server
- [PyMuPDF](https://pymupdf.readthedocs.io/) — PDF page rendering
- [PyYAML](https://pyyaml.org/) — config parsing
- [openpyxl](https://openpyxl.readthedocs.io/) — xlsx output (for aggregation)

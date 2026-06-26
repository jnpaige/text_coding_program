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

- **Left panel** — PDF page viewer with page navigation (arrow keys or buttons). Pages are scoped to the current investigation using the segment map.
- **Right panel** — Full codebook entry text for the current trait, plus data entry controls (radio buttons for binary, text input for numeric, radio buttons for categorical) and a justification field.
- **Unit selector** — Dropdown showing each `(trinomial, investigation)` pair from the segment map (e.g., "16VN1000 — Initial Survey (1997)").
- **Progress sidebar** — Shows which traits have been coded for the current investigation, with click-to-jump navigation.

Workflow: the coding unit is one investigation within one site form. For each unit, you code all traits, then advance to the next investigation. This matches site_coder's output structure exactly.

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

Sessions are resumable — existing `.coded.json` files are loaded when you revisit a trinomial.

## Shared input/output contract with site_coder

| | text_coding_program | site_coder |
|---|---|---|
| **Input: PDFs** | `pdf_dir` — renders `_ocr.pdf` for viewing | `md_input_dir` — reads `text_docling.txt` for LLM prompts |
| **Input: segments** | `segments_dir` — scopes pages per investigation | `segments_dir` — scopes text per investigation |
| **Input: codebook** | `codebook_dir` — displays entries for human coder | `codebook_dir` — injects entries into LLM prompts |
| **Output** | `<coder_id>/<tri>.coded.json` | `<model_slug>/<tri>.coded.json` |
| **Output schema** | `{trinomial, investigations: [{traits: [...]}]}` | identical |
| **Coding unit** | one investigation (from segment map) | one investigation (from segment map) |

The identical output schema means human and LLM results can be loaded into the same IRR comparison pipeline (Cohen's kappa, Krippendorff's alpha) without any format conversion.

## Distributing to a team

The program can be packaged as a standalone `.exe` bundle that requires no Python, uv, or terminal knowledge. Users double-click the exe and a browser opens.

### Building the bundle

```powershell
# Install the build dependency (one time)
uv sync --extra build

# Build the distributable folder
uv run python build.py --build
```

This produces `dist/text_coding_program/` (~70 MB) containing the exe, Python runtime, and all dependencies. To distribute:

```powershell
# Zip the folder
Compress-Archive -Path dist\text_coding_program -DestinationPath text_coding_program.zip
```

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

# text_coding_program

A browser-based interactive coding program for archaeologists, inspired by [McPherron's E4/Enterer](https://www.oldstoneage.com/osa/tech/e4/). This tool uses the same inputs as the LLM-based extraction pipeline — [pdf_ocr](https://github.com/jnpaige/pdf_ocr) output, codebook trait JSON files, and the page maps produced by [site_form_segmenter](https://github.com/jnpaige/site_form_segmenter) — and produces output in the same JSON format. The goal is to make it straightforward to compare human coding decisions against model coding decisions on the same documents and codebook, which is what inter-rater reliability analysis requires.

---

## How it fits into the pipeline

This program sits at the end of the same pipeline as the LLM coding tools. [pdf_ocr](https://github.com/jnpaige/pdf_ocr) converts the raw PDFs into searchable PDFs and page-indexed text files. [site_form_segmenter](https://github.com/jnpaige/site_form_segmenter) takes that output and builds a page map for each document, identifying which pages belong to which investigation and what kind of content each page contains. This program reads both: it renders the `_ocr.pdf` pages in the browser viewer and uses the segment map to scope the viewer to only the pages relevant to the current investigation and trait.

The codebook side works the same way. Codebook entries are parsed into per-trait JSON files by codebook_tools, and this program reads those files to display the codebook definition alongside the PDF page. The data entry controls (radio buttons for binary traits, text input for numeric, radio buttons for categorical) are generated from the trait's `data_type` field in the JSON.

---

## What it does

When you start the server and open the browser, you're presented with a project picker. You either create a new project by entering a name, your coder ID, and the paths to the three input directories, or you open an existing project and pick up where you left off. Projects are persistent — all your coding progress is saved in a `projects/` folder next to the program, and you can close and reopen the browser freely without losing work.

Once inside a project, the coding unit is one investigation within one site form, structured the same way as an LLM extraction call. The left panel shows the PDF page viewer; the right panel shows the codebook entry for the current trait alongside the data entry controls. You navigate pages with the arrow keys, enter a value with the number keys (1–9 select the corresponding option), add a brief justification, and press Enter to save and move to the next trait. The progress sidebar on the right shows which traits have been coded for the current investigation with click-to-jump navigation.

---

## Keyboard shortcuts

Enter saves the current entry and advances to the next trait. Escape skips to the next trait without saving. Left and right arrow keys flip PDF pages. The number keys 1 through 9 select the corresponding radio button option for the current trait. Plus and minus zoom the PDF in and out; 0 resets to fit width. Ctrl+scroll wheel also zooms.

---

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

`uv.lock` is committed to the repo, so every machine gets identical package versions. If you prefer to activate the venv manually, run `.venv\Scripts\activate` on Windows or `source .venv/bin/activate` on Mac/Linux, then use `python server.py` directly.

### 3. Start the server

```powershell
uv run python server.py
```

The server auto-finds an open port if the default (8090) is busy. Open the URL it prints in your browser, and the project picker will appear.

---

## Output

Coded results are saved in a `projects/` folder next to the server. Each project gets its own directory containing a `coded/` subdirectory with one `.coded.json` file per site. The JSON structure matches what site_coder produces:

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
  "project": "Kisatchie chunk 3",
  "first_saved": "2026-06-20T...",
  "last_saved": "2026-06-20T..."
}
```

The identical output schema means human and LLM results can be loaded into the same inter-rater reliability pipeline (Cohen's kappa, Krippendorff's alpha) without any format conversion.

---

## Distributing to a team

The program can be packaged as a standalone `.exe` bundle that requires no Python, uv, or terminal knowledge. Users double-click the exe, a browser opens to the project picker, and they can start coding immediately. No installation required beyond unzipping.

To build the bundle:

```powershell
# Install the build dependency (one time)
uv sync --extra build

# Build the distributable folder (~70 MB)
uv run python build.py --build
```

This produces `dist/text_coding_program/`. Zip that folder and share it. End users unzip, double-click `text_coding_program.exe`, and their browser opens to the project picker. Projects are saved in a `projects/` folder next to the exe and persist across sessions.

---

## Dependencies

- [FastAPI](https://fastapi.tiangolo.com/) — web framework
- [uvicorn](https://www.uvicorn.org/) — ASGI server
- [PyMuPDF](https://pymupdf.readthedocs.io/) — PDF page rendering
- [openpyxl](https://openpyxl.readthedocs.io/) — xlsx output
- [PyYAML](https://pyyaml.org/) — config parsing
- [PyInstaller](https://pyinstaller.org/) — packaging (build dependency only)

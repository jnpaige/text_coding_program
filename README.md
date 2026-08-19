# text_coding_program

This is a browser-based interactive text coding against structured codebooks, inspired by [McPherron's E4/Enterer Trois](https://www.oldstoneage.com/osa/tech/e4/). It takes: a text corpus run through pdf_ocr, a page map that reports which pages in each pdf need coding, and codebook entries pulled out of a natural-language codebook by codebook_tools. A coder works from one window. The page, the codebook entry, and the entry field sit together, which keeps attention on the text and keeps entry errors down.


## Pipeline context

This tool sits at the end of a multi-repo pipeline. Each repo upstream writes what the next one reads:

```
pdf_ocr ──────► site_form_segmenter ──────► text_coding_program
                                      └───► site_coder (LLM batch)
codebook_tools ───────────────────────────►
```

See [pdf_ocr](https://github.com/jnpaige/pdf_ocr) for more on the workflow.


## What this program does

It serves a single-page web app on your own machine with four parts:

- Left panel — the PDF is displayed here one page at a time, with arrow keys or buttons to flip between pages. Pages are scoped to the segment you are coding.
- Right panel — the full codebook entry for the current trait, the entry controls for it, and a justification field. Binary traits get radio buttons, numeric traits a text box, categorical traits trigger radio buttons, lists get checkboxes.
- Unit selector — a dropdown of every document-and-segment pair, like "16VN1000 — Initial Survey (1997)".
- Progress sidebar — which traits are done for this segment. Click one to jump to it.

The unit of work is one segment inside one document. A segment is whatever the segment file says it is. For a site form it is usually one investigation. For a report it might be a chapter. The program cycles through every code for every trait, and cycles through every segment of every document and saves the resulting decisions. 

Each coding project can be saved as its own project, with unique identifiers, and associated with particular coders given by "coder id". 

### Keyboard shortcuts

| Key | Action |
|---|---|
| Enter | Save current trait and advance to next |
| Esc | Skip to next trait without saving |
| ← → | Previous / next PDF page |

## Setup

### 1. Install uv and install dependencies

```powershell
# Windows — install uv once per machine
winget install astral-sh.uv
```

```bash
# Mac/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

We then just use uv to install dependencies onto the local machine. 

```powershell
uv sync
```

`uv.lock` is committed, so every machine gets the same package versions.


## Distributing to a team

The program can be packaged as a standalone `.exe` bundle carrying its own Python runtime. A user double-clicks the exe and a browser opens.

### Building the bundle

```powershell
uv run --extra build python build.py --build
```

Pass `--extra build` on the build command itself. Every `uv run` resyncs the environment to the project's default dependency set, so a plain `uv run python server.py` in between prunes PyInstaller back out of `.venv`, and the next build dies with `No module named PyInstaller`. `build.py` digs itself out of this by re-invoking PyInstaller through `uv run --extra build` when it cannot import it, and passing the extra up front saves the round trip. This is pretty hacky but... it works.

The build produces `dist/text_coding_program/`, about 80 MB, holding the exe, the Python runtime, and every dependency. A `Text Coding Program.lnk` shortcut lands next to it in `dist/`. The build script pops up a message box saying where the shortcut went, so you can move it to the Desktop or the taskbar or the Start menu. But, if you subsequently move the exe anywhere it breaks the shortcut connection. 

The build also writes `READ_ME_FIRST.txt` into the bundle and prints the exe's SHA-256. Both exist for one reason: an unsigned tool from a colleague trips a SmartScreen warning, and a coder who was told to expect it, and handed a hash to check, can decide for themselves.


# Using the program. 

## Installation

1. Unzip `text_coding_program.zip` anywhere
2. Double-click `text_coding_program.exe`. If the build is unsigned, expect a SmartScreen prompt the first time — "More info", then "Run anyway"
3. A browser opens to the project picker. Make a new project or pick up an old one
4. Close the console window to stop the server

## Writing your own segment file

A segment file is a small map. It says which pages of a document belong to which unit of coding. site_form_segmenter writes these with the help of an open weight LLM. You can also write one by hand, which for a small set of documents is often the shortest road.

Put one file per document in `segments_dir`, named `<document id>.segments.json`. The document ID is the name of the document's folder under `pdf_dir`, or the PDF's own filename when the PDFs sit loose in one folder. Here is a whole file:

```json
{
  "trinomial": "16VN1451",
  "segments": [
    { "label": "1995 Phase I Survey", "year": 1995, "pages": [0, 1] },
    { "label": "1994 SCIAA Survey Report", "year": 1994, "pages": [2, 3, 4, 5] }
  ]
}
```

A segment needs a label and a list of pages. Everything past that is optional. Five rules cover the rest.

Page numbers start at zero. The first page of the PDF is page 0. PDFs processed with pdf_ocr have a page number following this standards added to the top right of each page. 

Give every segment a label, and make the labels different from each other inside one file. The label is what the program matches on when it saves, so two segments sharing a label will write over each other's traits.

`year` is optional. Leave it out and the unit selector shows a question mark where the year would go.

`trinomial` names the document. `report`, `document`, and `item` do the same job, and the program reads all four, since the site form, report, and GLO segmenters each picked a different one. With none of them present the ID comes from the filename, with any `<model_slug>__` prefix and the `.segments` suffix taken off.

Named page groups narrow the viewer to a slice of the `pages` list. Any key ending in `_pages` counts as one, and the names are yours to pick. The site-form names `form_pages`, `narrative_pages`, and `nrhp_pages` work by that same rule:

```json
{ "label": "1995 Phase I Survey", "year": 1995,
  "pages": [0, 1, 2], "artifact_pages": [1], "feature_pages": [2] }
```

With groups present the viewer shows the union of the groups, here pages 1 and 2, and skips page 0. With no groups it shows all three.

To code whole documents, leave the segments directory blank on the project form. Every document becomes one unit covering every page of the PDF.

One thing to check before you start. The document IDs in your segment files have to match the ones the program reads out of `pdf_dir`. Those come from the subfolder names, and from the PDF filenames when `pdf_dir` holds loose PDFs instead of one folder per document. The Document ID pattern on the project form narrows a name down to its first capture group, so the default `(\d{2}[A-Z]{2}\d+)` pulls `16VN1000` out of a folder named `16VN1000_Smith_1997`. Clear that field and names are taken whole, which is the setting for papers, abstracts, and reports.

## Output

Output follows site_coder's run directory convention, with the coder ID standing where the model name goes:

```
runs/
  YYYYMMDD_HHMM_gitsha/
    jpaige/                         coder ID folder
      16VN1000.coded.json
      16VN1001.coded.json
      ...
```

Each `.coded.json` holds the coding metadata and one entry per segment coded:

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

Saving merges on `segment_label`. Coding one segment of a document leaves the other segments' saved traits alone.

Sessions resume. Existing `.coded.json` files are read back when you return to a document.

## Project provenance

Each project's `project.json`, in `projects/<slug>/`, carries a `provenance` block. It is written once when the project is created and never read again:

```json
"provenance": {
  "captured_at": "2026-08-11T...",
  "note": "Frozen at project creation — not re-read; may not reflect the source directories' current contents if they were later modified or moved.",
  "codebook_summary": { "codebook_name": "Site_Form", "codebook_version": "v1.1", "code_ids": [...] },
  "run_metadata": { "run_id": "...", "mode": "text", "text_model": "qwen2.5:14b", ... },
  "config_snapshot": { "mode": "text", "temperature": 0.2, ... }
}
```

`run_metadata` and `config_snapshot` come from the segmenter run's own `run_metadata.json` and its config snapshot. The program looks in `segments_dir` itself, then in its parent, which covers both the flat run-folder layout and the older per-model-subfolder one. A run that never wrote one simply has no block, and older runs predate the whole idea.

Path-shaped keys like `input_dir` and `codebook_file` are stripped out of `config_snapshot`. Those paths belong to whatever machine the run was made on. The block stands as a record of what happened. The live `pdf_dir`, `codebook_dir`, and `segments_dir` fields elsewhere in `project.json` are where the program reads from.

## Input/output contract with site_coder

Checked against site_coder's source, `coder.py`, `batch_coder.py`, and `lib/reporter.py`, on 2026-08-11.

| | text_coding_program | site_coder |
|---|---|---|
| Input: PDFs | `pdf_dir` — renders `_ocr.pdf` for viewing | `md_input_dir` — reads `text_docling.txt` for LLM prompts |
| Input: segments | `segments_dir` — scopes pages per segment | `segments_dir` — scopes text per segment |
| Input: codebook | `codebook_dir` — displays entries for human coder | `codebook_dir` — injects entries into LLM prompts |
| Output | `<coder_id>/<tri>.coded.json` | `<model_slug>/<tri>.coded.json` |
| Output schema | `{trinomial, segments: [{segment_label, segment_year, traits: [...]}]}` | `{trinomial, segments: [{label, year, codes: {...}}]}` |
| Coding unit | one segment (from segment map) | one segment (from segment map) |

The two raw formats diverge. site_coder keeps the segmenter's `label`, `year`, and `pages` fields unprefixed and stores trait values as a flat `codes: {trait_key: value}` dict. This tool prefixes those fields and stores each trait as an object carrying justification, confidence, and evidence pages. Both share the top-level `segments` array and the column name `segment_label`, which site_coder's per-run CSV export already used.

Comparing coders against models, for Cohen's kappa or Krippendorff's alpha, means flattening both into one shape. site_coder does that per run in `build_coded_csv`. This side does it in project export, which writes `coded_data.csv` keyed on `trinomial`, `segment_key`, and `trait_key`. See below.

One loose end sits on the site_coder side: `merge_results.py`, a secondary batch-merge script, still writes `investigation_label` where `reporter.py` writes `segment_label`.

## Exporting results

"Export…" on any project card in the picker writes a folder you can copy to another computer and read:

```
<project>_export_<timestamp>/
  coded_data.csv       one row per coded trait — the file to open in Excel/R/pandas
  project_export.json  the same data in nested form
  coded/               the original per-trinomial .coded.json files, unmodified
  codebook/            per-trait JSON, so trait definitions travel with the results
  project.json         project metadata and the source paths this export came from
  manifest.json        counts, contents, SHA-256 of the CSV and JSON
  README.txt           what the folder is, written for whoever receives it
```

Open `coded_data.csv` on the receiving machine. Excel, R, and pandas all read it as it stands.

### Local access control

The server is a browser-reachable window onto the coder's filesystem. It renders PDFs, writes coded output, and opens a native folder-picker dialog. Binding to `127.0.0.1` keeps it off the network. Inside the machine it stays open. Every process running as that user can reach it, and any web page the coder has open can make their browser send it requests.

Three checks in `_local_guard()`, in `server.py`, close that gap:

| Check | Blocks |
| --- | --- |
| Per-run session token (HttpOnly, `SameSite=Strict` cookie; `X-Session-Token` also accepted) | Other local processes and users driving the server |
| Host header allow-list | DNS rebinding — a remote page pointing a hostname it controls at loopback |
| Origin check | Cross-site requests from any page the coder has open |

Three more things hold. `--host` refuses non-loopback addresses unless you also pass `--allow-non-loopback`, because serving this on a LAN serves the documents being coded. The save endpoint accepts only the trinomials the loaded project actually discovered, so a crafted request cannot steer a write out of the project folder. FastAPI's `/docs` and `/openapi.json` are off.

One limit is known and unfixed. Cookies are not scoped to a port, so a different local server on another port could be handed the session cookie if the browser navigates to it. That takes an attacker already running code as the coder, which is past what a single-user local tool can defend.

## Dependencies

- [FastAPI](https://fastapi.tiangolo.com/) — web framework
- [uvicorn](https://www.uvicorn.org/) — ASGI server
- [PyMuPDF](https://pymupdf.readthedocs.io/) — PDF page rendering
- [openpyxl](https://openpyxl.readthedocs.io/) — xlsx output
- [PyYAML](https://pyyaml.org/) — config parsing
- [PyInstaller](https://pyinstaller.org/) — packaging (build dependency only)

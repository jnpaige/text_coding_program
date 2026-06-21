For session management instructions:
`C:\Users\jpaige\Desktop\Research_repositories\Context_instructions\universal-context-management.md`

## What this repo does

Browser-based interactive text coding program for coding archaeological site forms against structured codebooks. Serves a FastAPI app locally that renders PDF pages on the fly, displays codebook entries alongside, and saves coded results in the same JSON format as `site_coder`. Segment maps from `site_form_segmenter` optionally filter pages to the relevant investigation. Designed for human inter-rater reliability comparison with LLM-coded output.

## Structure

```
server.py              FastAPI app — serves HTML, renders PDF pages, saves results
config.yaml            paths to PDFs, codebook, segments, server settings
static/
  index.html           single-page frontend (vanilla HTML/CSS/JS, no build step)
runs/                  gitignored — coded output per session
```

## Dependencies and setup

```powershell
uv sync
uv run python server.py
```

Opens at `http://127.0.0.1:8080`. Prompts for coder ID on startup.

## Key design

- Output JSON matches `site_coder` format (`trinomial`, `investigations[]`, `traits[]` with `trait_key`, `trait_value`, `confidence`, `justification`, `evidence_pages`) plus `coder_id`, `first_saved`, `last_saved`.
- Session is resumable — existing `.coded.json` files are loaded on trinomial select.
- No LLM needed — all coding is human. No Ollama dependency.
- PDF pages rendered via PyMuPDF at configurable DPI. Pages are served as PNG on demand (not pre-rendered).

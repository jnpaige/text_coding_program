For session management instructions:
`C:\Users\jpaige\Desktop\Research_repositories\Context_instructions\universal-context-management.md`

## What this repo does

Browser-based interactive text coding program for coding archaeological documents against structured codebooks. Serves a FastAPI app locally that renders PDF pages on the fly, displays codebook entries alongside, and saves coded results with the same top-level `segments` array key as `site_coder` (verified against its source 2026-08-11) — see README's "Input/output contract with site_coder" for the parts that do and don't match. Segment maps from `site_form_segmenter` optionally filter pages to the relevant segment — a segment isn't necessarily a site investigation (site_form_segmenter's own schema is generic; segments can also be report structural sections or narrowed per-trinomial passes). Designed for human inter-rater reliability comparison with LLM-coded output.

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

- Output JSON: `trinomial`, `segments[]` (each with `segment_label`, `segment_year`, `traits[]` — `trait_key`, `trait_value`, `confidence`, `justification`, `evidence_pages`) plus `coder_id`, `first_saved`, `last_saved`. Saving merges into existing segments by `segment_label` rather than overwriting the whole array.
- Page scoping is generic: any segment key ending in `_pages` (not just the hardcoded site-form set) is collected into `page_groups` and unioned to build the coder's page list. Works for site-form segments and report-structural-pass segments without code changes per segment type.
- Session is resumable — existing `.coded.json` files are loaded on trinomial select.
- `project.json` carries a frozen `provenance` block (codebook identity, and the segmenter run's `run_metadata.json`/config snapshot when available) captured once at project creation — see README's "Project provenance" section. Never re-read; not authoritative for locating files.
- No LLM needed — all coding is human. No Ollama dependency.
- PDF pages rendered via PyMuPDF at configurable DPI. Pages are served as PNG on demand (not pre-rendered).

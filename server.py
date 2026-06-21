#!/usr/bin/env python3
"""
server.py — Browser-based interactive text coding program.

Serves a single-page app for coding site forms against a structured codebook.
PDF pages are rendered on the fly; codebook entries are displayed alongside.
Segment maps (from site_form_segmenter) optionally filter pages per investigation.

Output matches site_coder's JSON structure so results can be compared directly.

Usage:
    uv run python server.py
    uv run python server.py --config config.yaml
"""

import argparse
import csv
import io
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pymupdf
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
import uvicorn

# ---------------------------------------------------------------------------
# Globals (set in main)
# ---------------------------------------------------------------------------

app = FastAPI()
_cfg: dict = {}
_traits: list[dict] = []
_trinomials: list[str] = []
_segments: dict[str, dict] = {}
_pdf_cache: dict[str, Path] = {}
_output_dir: Path = Path("runs")
_run_dir: Path | None = None
_coder_id: str = ""
_page_dpi: int = 150


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def _load_traits(codebook_dir: Path) -> list[dict]:
    traits = []
    for p in sorted(codebook_dir.glob("*.json")):
        if p.name.startswith("_") or p.name.startswith("codebook_summary"):
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        if not data.get("full_text"):
            continue
        traits.append(data)
    return traits


def _discover_trinomials(pdf_dir: Path, pattern: str) -> list[str]:
    rx = re.compile(pattern)
    tris = set()
    for d in sorted(pdf_dir.iterdir()):
        if d.is_dir():
            m = rx.search(d.name)
            if m:
                tris.add(m.group(1))
    return sorted(tris)


def _find_pdf(pdf_dir: Path, tri: str) -> Path | None:
    tri_dir = pdf_dir / tri
    if tri_dir.is_dir():
        for suffix in ("_ocr.pdf", ".pdf"):
            candidate = tri_dir / f"{tri}{suffix}"
            if candidate.exists():
                return candidate
        for f in tri_dir.glob("*.pdf"):
            return f
    for f in pdf_dir.glob(f"{tri}*.pdf"):
        return f
    return None


def _load_segments(segments_dir: Path, pattern: str) -> dict[str, dict]:
    if not segments_dir or not segments_dir.is_dir():
        return {}
    segs = {}
    for p in segments_dir.glob("*.segments.json"):
        data = json.loads(p.read_text(encoding="utf-8"))
        tri = data.get("trinomial", re.search(pattern, p.stem).group(1) if re.search(pattern, p.stem) else p.stem)
        segs[tri] = data
    return segs


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).parent, stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return "nogit"


def _ensure_run_dir() -> Path:
    global _run_dir
    if _run_dir is None:
        stamp = f"{datetime.now().strftime('%Y%m%d_%H%M')}_{_git_sha()}"
        _run_dir = _output_dir / stamp
        _run_dir.mkdir(parents=True, exist_ok=True)
    return _run_dir


def _coder_dir() -> Path:
    run_dir = _ensure_run_dir()
    d = run_dir / _coder_id
    d.mkdir(exist_ok=True)
    return d


def _load_existing_results(tri: str) -> dict | None:
    p = _coder_dir() / f"{tri}.coded.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/session")
async def get_session():
    return {
        "coder_id": _coder_id,
        "trinomials": _trinomials,
        "traits": [{"code_id": t["code_id"], "title": t.get("title", t["code_id"]),
                     "data_type": t.get("data_type", "binary"),
                     "categories": t.get("categories", "")} for t in _traits],
        "has_segments": bool(_segments),
        "run_dir": str(_ensure_run_dir()),
    }


@app.get("/api/trinomial/{tri}")
async def get_trinomial(tri: str):
    if tri not in _pdf_cache:
        raise HTTPException(404, f"Trinomial not found: {tri}")

    seg_data = _segments.get(tri)
    segments = seg_data.get("segments", []) if seg_data else []

    pdf_path = _pdf_cache[tri]
    doc = pymupdf.open(str(pdf_path))
    n_pages = len(doc)
    doc.close()

    existing = _load_existing_results(tri)
    coded_traits: dict[str, dict] = {}
    if existing:
        for inv in existing.get("investigations", []):
            inv_key = inv.get("investigation_label") or "all"
            for tr in inv.get("traits", []):
                coded_traits[f"{inv_key}::{tr['trait_key']}"] = tr

    return {
        "trinomial": tri,
        "n_pages": n_pages,
        "segments": segments,
        "coded_traits": coded_traits,
    }


@app.get("/api/page/{tri}/{page_num}")
async def get_page_image(tri: str, page_num: int):
    if tri not in _pdf_cache:
        raise HTTPException(404, f"Trinomial not found: {tri}")
    pdf_path = _pdf_cache[tri]
    doc = pymupdf.open(str(pdf_path))
    if page_num < 0 or page_num >= len(doc):
        doc.close()
        raise HTTPException(404, f"Page {page_num} out of range")
    page = doc[page_num]
    pix = page.get_pixmap(dpi=_page_dpi)
    img_bytes = pix.tobytes("png")
    doc.close()
    return Response(content=img_bytes, media_type="image/png")


@app.get("/api/trait/{code_id}")
async def get_trait(code_id: str):
    for t in _traits:
        if t["code_id"] == code_id:
            return t
    raise HTTPException(404, f"Trait not found: {code_id}")


@app.post("/api/save/{tri}")
async def save_result(tri: str, body: dict):
    """Save or update coded results for a trinomial."""
    coder_dir = _coder_dir()
    out_path = coder_dir / f"{tri}.coded.json"

    existing = None
    if out_path.exists():
        existing = json.loads(out_path.read_text(encoding="utf-8"))

    now = datetime.now(timezone.utc).isoformat()

    result = {
        "trinomial": tri,
        "investigations": body.get("investigations", []),
        "coder_id": _coder_id,
        "first_saved": existing["first_saved"] if existing else now,
        "last_saved": now,
    }

    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"status": "saved", "path": str(out_path)}


@app.get("/api/progress")
async def get_progress():
    """Return coding progress across all trinomials."""
    coder_dir = _coder_dir()
    coded = {}
    for p in coder_dir.glob("*.coded.json"):
        data = json.loads(p.read_text(encoding="utf-8"))
        tri = data["trinomial"]
        n_traits_coded = 0
        n_traits_total = 0
        for inv in data.get("investigations", []):
            for tr in inv.get("traits", []):
                n_traits_total += 1
                if tr.get("trait_value") is not None:
                    n_traits_coded += 1
        coded[tri] = {"coded": n_traits_coded, "total": n_traits_total}

    n_traits = len(_traits)
    total_trinomials = len(_trinomials)
    completed = sum(1 for tri in _trinomials if tri in coded
                    and coded[tri]["coded"] >= coded[tri]["total"] and coded[tri]["total"] > 0)

    return {
        "trinomials_total": total_trinomials,
        "trinomials_coded": completed,
        "n_traits": n_traits,
        "per_trinomial": coded,
    }


app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global _cfg, _traits, _trinomials, _segments, _pdf_cache
    global _output_dir, _coder_id, _page_dpi

    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--coder", default=None, help="Coder ID (prompted if not given)")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        sys.exit(f"Config not found: {cfg_path}")
    _cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))

    pdf_dir = Path(_cfg["pdf_dir"])
    codebook_dir = Path(_cfg["codebook_dir"])
    pattern = _cfg.get("trinomial_pattern", r"(\d{2}[A-Z]{2}\d+)")

    _traits = _load_traits(codebook_dir)
    if not _traits:
        sys.exit(f"No traits found in: {codebook_dir}")

    _trinomials = _discover_trinomials(pdf_dir, pattern)
    if not _trinomials:
        sys.exit(f"No trinomials found in: {pdf_dir}")

    for tri in _trinomials:
        pdf = _find_pdf(pdf_dir, tri)
        if pdf:
            _pdf_cache[tri] = pdf

    seg_dir = _cfg.get("segments_dir")
    if seg_dir:
        _segments = _load_segments(Path(seg_dir), pattern)
        print(f"Segments   : {len(_segments)} trinomials loaded")

    _output_dir = Path(_cfg.get("output_dir", "runs"))
    _page_dpi = _cfg.get("page_dpi", 150)

    _coder_id = args.coder
    if not _coder_id:
        _coder_id = input("Enter coder ID: ").strip()
    if not _coder_id:
        sys.exit("Coder ID is required.")

    print(f"Coder      : {_coder_id}")
    print(f"PDFs       : {len(_pdf_cache)} found")
    print(f"Traits     : {len(_traits)}")
    print(f"Trinomials : {len(_trinomials)}")
    print(f"Server     : http://{_cfg.get('host', '127.0.0.1')}:{_cfg.get('port', 8080)}")

    uvicorn.run(app, host=_cfg.get("host", "127.0.0.1"), port=_cfg.get("port", 8080))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
server.py — Browser-based interactive text coding program.

Serves a single-page app for coding site forms against a structured codebook.
Projects are persistent — create once, resume across sessions.

Usage:
    uv run python server.py
    uv run python server.py --port 8090
"""

import argparse
import asyncio
import json
import re
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

import pymupdf
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
import uvicorn

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------

app = FastAPI()

if getattr(sys, 'frozen', False):
    _base_dir = Path(sys.executable).parent
    _internal_dir = _base_dir / "_internal"
else:
    _base_dir = Path(__file__).parent
    _internal_dir = _base_dir

_projects_dir = _base_dir / "projects"
_static_dir = _internal_dir / "static"

_project: dict | None = None
_traits: list[dict] = []
_trinomials: list[str] = []
_segments: dict[str, dict] = {}
_pdf_cache: dict[str, Path] = {}
_page_dpi: int = 150


# ---------------------------------------------------------------------------
# Data loading helpers
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


def _coded_dir() -> Path:
    d = Path(_project["project_dir"]) / "coded"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_project_data(proj: dict) -> None:
    """Load traits, trinomials, segments, PDFs for the given project."""
    global _project, _traits, _trinomials, _segments, _pdf_cache, _page_dpi

    _project = proj
    pattern = proj.get("trinomial_pattern", r"(\d{2}[A-Z]{2}\d+)")
    _page_dpi = proj.get("page_dpi", 150)

    pdf_dir = Path(proj["pdf_dir"])
    codebook_dir = Path(proj["codebook_dir"])

    _traits = _load_traits(codebook_dir)
    _trinomials = _discover_trinomials(pdf_dir, pattern)

    _pdf_cache.clear()
    for tri in _trinomials:
        pdf = _find_pdf(pdf_dir, tri)
        if pdf:
            _pdf_cache[tri] = pdf

    seg_dir = proj.get("segments_dir")
    _segments = _load_segments(Path(seg_dir), pattern) if seg_dir else {}

    proj["last_opened"] = datetime.now(timezone.utc).isoformat()
    _save_project(proj)


def _save_project(proj: dict) -> None:
    proj_path = Path(proj["project_dir"]) / "project.json"
    proj_path.write_text(json.dumps(proj, indent=2, ensure_ascii=False), encoding="utf-8")


def _count_progress(proj: dict) -> dict:
    """Count coded units for a project without fully loading it."""
    coded_dir = Path(proj["project_dir"]) / "coded"
    if not coded_dir.is_dir():
        return {"coded_files": 0, "coded_traits": 0}
    coded_files = list(coded_dir.glob("*.coded.json"))
    n_traits = 0
    for f in coded_files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            for seg in data.get("segments", []):
                n_traits += len(seg.get("traits", []))
        except Exception:
            pass
    return {"coded_files": len(coded_files), "coded_traits": n_traits}


# ---------------------------------------------------------------------------
# API — Project management
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(_static_dir / "index.html")


def _config_snapshot_no_paths(cfg: dict) -> dict:
    """Strip path-shaped keys (input_dir, codebook_file, etc.) from a config
    snapshot — the paths in a run's own config.yaml describe where that run's
    creator found its inputs, which may not exist or may be stale relative to
    wherever segments_dir/codebook_dir actually point to now."""
    return {k: v for k, v in cfg.items() if not re.search(r"dir|file|path", k, re.I)}


def _find_run_provenance(segments_dir: Path) -> dict | None:
    """Look for a segmenter run's frozen run_metadata.json + config snapshot,
    checking segments_dir itself (current flat run-folder layout) and its
    parent (older per-model-subfolder layout). Returns None if neither has one
    — that's an accurate "not available for this run", not an error."""
    for candidate in (segments_dir, segments_dir.parent):
        meta_path = candidate / "run_metadata.json"
        if not meta_path.is_file():
            continue
        result = {"run_metadata": json.loads(meta_path.read_text(encoding="utf-8"))}
        config_files = [p for p in candidate.glob("*.yaml") if p.name != "prompts.yaml"]
        if config_files:
            cfg = yaml.safe_load(config_files[0].read_text(encoding="utf-8")) or {}
            result["config_snapshot"] = _config_snapshot_no_paths(cfg)
        return result
    return None


def _find_codebook_summary(codebook_dir: Path) -> dict | None:
    """codebook_tools writes a codebook_summary_<version>.json (name, version,
    parse_date, code_ids) alongside each codebook's per-trait JSON files — pure
    identity info, no paths, safe to snapshot as-is."""
    matches = list(codebook_dir.glob("codebook_summary_*.json"))
    if not matches:
        return None
    return json.loads(matches[0].read_text(encoding="utf-8"))


@app.get("/api/projects")
async def list_projects():
    _projects_dir.mkdir(exist_ok=True)
    projects = []
    for d in sorted(_projects_dir.iterdir()):
        pf = d / "project.json"
        if pf.exists():
            proj = json.loads(pf.read_text(encoding="utf-8"))
            proj["progress"] = _count_progress(proj)
            projects.append(proj)
    return projects


@app.post("/api/projects")
async def create_project(body: dict):
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(400, "Project name is required")

    slug = re.sub(r'[^\w\s-]', '', name.lower()).strip()
    slug = re.sub(r'\s+', '_', slug)

    proj_dir = _projects_dir / slug
    if proj_dir.exists():
        raise HTTPException(409, f"Project '{slug}' already exists")

    for key in ("pdf_dir", "codebook_dir"):
        p = Path(body.get(key, ""))
        if not p.is_dir():
            raise HTTPException(400, f"{key} not found: {p}")

    proj_dir.mkdir(parents=True)
    (proj_dir / "coded").mkdir()

    # Frozen provenance snapshot — captured once, at creation time, and never
    # re-read afterward. Kept separate from the live pdf_dir/codebook_dir/
    # segments_dir fields below, which are what the program actually operates
    # on: this block can go stale if the source directories move, but nothing
    # here ever gets treated as authoritative for locating files.
    provenance = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "note": ("Frozen at project creation — not re-read; may not reflect "
                 "the source directories' current contents if they were "
                 "later modified or moved."),
        "codebook_summary": _find_codebook_summary(Path(body["codebook_dir"])),
    }
    segments_dir_val = body.get("segments_dir", "")
    if segments_dir_val:
        run_provenance = _find_run_provenance(Path(segments_dir_val))
        if run_provenance:
            provenance.update(run_provenance)

    proj = {
        "name": name,
        "slug": slug,
        "coder_id": body.get("coder_id", ""),
        "pdf_dir": body["pdf_dir"],
        "codebook_dir": body["codebook_dir"],
        "segments_dir": segments_dir_val,
        "trinomial_pattern": body.get("trinomial_pattern", r"(\d{2}[A-Z]{2}\d+)"),
        "page_dpi": int(body.get("page_dpi", 150)),
        "project_dir": str(proj_dir),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_opened": datetime.now(timezone.utc).isoformat(),
        "provenance": provenance,
    }
    _save_project(proj)
    return proj


def _browse_folder_dialog(initial_dir: str = "") -> str:
    """Open a native OS folder-picker dialog and return the chosen path (empty
    string if the user cancels). Only meaningful because this server and the
    browser hitting it are always the same machine (127.0.0.1, one process
    per user) — a real hosted deployment couldn't do this, the dialog would
    pop up on the server's screen, not the client's."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.update()
    path = filedialog.askdirectory(initialdir=initial_dir or None, parent=root)
    root.destroy()
    return path


@app.post("/api/browse-folder")
async def browse_folder(body: dict):
    initial_dir = body.get("initial_dir", "")
    try:
        loop = asyncio.get_event_loop()
        path = await loop.run_in_executor(None, _browse_folder_dialog, initial_dir)
    except Exception as e:
        raise HTTPException(500, f"Could not open folder browser: {e}")
    return {"path": path}


@app.post("/api/projects/load")
async def load_project(body: dict):
    slug = body.get("slug", "")
    proj_path = _projects_dir / slug / "project.json"
    if not proj_path.exists():
        raise HTTPException(404, f"Project not found: {slug}")

    proj = json.loads(proj_path.read_text(encoding="utf-8"))
    _load_project_data(proj)

    return {"status": "loaded", "name": proj["name"], "trinomials": len(_trinomials),
            "traits": len(_traits), "segments": len(_segments)}


# ---------------------------------------------------------------------------
# API — Coding (requires loaded project)
# ---------------------------------------------------------------------------

def _require_project():
    if _project is None:
        raise HTTPException(400, "No project loaded")


@app.get("/api/session")
async def get_session():
    _require_project()

    work_queue = []
    for tri in _trinomials:
        if tri not in _pdf_cache:
            continue
        seg_data = _segments.get(tri)
        if seg_data and seg_data.get("segments"):
            for seg in seg_data["segments"]:
                # Page-type sidecars vary by segment type (form_pages/narrative_pages/
                # nrhp_pages for site forms; dynamically-named <section_type>_pages for
                # report structural passes) — collect whatever's actually present rather
                # than assuming fixed names, so this works for any segmenter output.
                page_groups = {k: v for k, v in seg.items()
                               if k.endswith("_pages") and k != "pages"}
                work_queue.append({
                    "trinomial": tri,
                    "segment_label": seg.get("label"),
                    "segment_year": seg.get("year"),
                    "pages": sorted(seg.get("pages", [])),
                    "page_groups": page_groups,
                })
        else:
            work_queue.append({
                "trinomial": tri,
                "segment_label": None,
                "segment_year": None,
                "pages": None,
                "page_groups": {},
            })

    return {
        "project_name": _project["name"],
        "coder_id": _project["coder_id"],
        "work_queue": work_queue,
        "traits": [{"code_id": t["code_id"], "title": t.get("title", t["code_id"]),
                     "data_type": t.get("data_type", "binary"),
                     "categories": t.get("categories", "")} for t in _traits],
        "has_segments": bool(_segments),
    }


@app.get("/api/trinomial/{tri}")
async def get_trinomial(tri: str):
    _require_project()
    if tri not in _pdf_cache:
        raise HTTPException(404, f"Trinomial not found: {tri}")

    seg_data = _segments.get(tri)
    segment_defs = seg_data.get("segments", []) if seg_data else []

    pdf_path = _pdf_cache[tri]
    doc = pymupdf.open(str(pdf_path))
    n_pages = len(doc)
    doc.close()

    coded_traits: dict[str, dict] = {}
    out_path = _coded_dir() / f"{tri}.coded.json"
    if out_path.exists():
        existing = json.loads(out_path.read_text(encoding="utf-8"))
        for seg in existing.get("segments", []):
            seg_key = seg.get("segment_label") or "all"
            for tr in seg.get("traits", []):
                coded_traits[f"{seg_key}::{tr['trait_key']}"] = tr

    return {
        "trinomial": tri,
        "n_pages": n_pages,
        "segment_defs": segment_defs,
        "coded_traits": coded_traits,
    }


@app.get("/api/page/{tri}/{page_num}")
async def get_page_image(tri: str, page_num: int):
    _require_project()
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
    _require_project()
    for t in _traits:
        if t["code_id"] == code_id:
            return t
    raise HTTPException(404, f"Trait not found: {code_id}")


@app.post("/api/save/{tri}")
async def save_result(tri: str, body: dict):
    _require_project()
    out_path = _coded_dir() / f"{tri}.coded.json"

    existing = None
    if out_path.exists():
        existing = json.loads(out_path.read_text(encoding="utf-8"))

    now = datetime.now(timezone.utc).isoformat()

    # Merge incoming segments into existing ones by segment_label rather than
    # replacing the whole array — the client only ever round-trips the
    # segment label(s) currently in view, so a naive overwrite would silently
    # discard previously-saved segments for this trinomial. segment_label is
    # treated as an opaque key here — it works the same whether a segment is
    # a site investigation, a report structural section, or a narrowed
    # per-trinomial pass; nothing here assumes what kind of segment it is.
    by_label: dict = {}
    if existing:
        for seg in existing.get("segments", []):
            by_label[seg.get("segment_label")] = seg
    for seg in body.get("segments", []):
        by_label[seg.get("segment_label")] = seg

    result = {
        "trinomial": tri,
        "segments": list(by_label.values()),
        "coder_id": _project["coder_id"],
        "project": _project["name"],
        "first_saved": existing["first_saved"] if existing else now,
        "last_saved": now,
    }

    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"status": "saved", "path": str(out_path)}


app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _find_open_port(host: str, preferred: int, max_attempts: int = 20) -> int:
    for offset in range(max_attempts):
        port = preferred + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
                if offset > 0:
                    print(f"[note] Port {preferred} in use — using {port} instead")
                return port
            except OSError:
                continue
    sys.exit(f"Could not find an open port in range {preferred}–{preferred + max_attempts - 1}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8090)
    args = ap.parse_args()

    _projects_dir.mkdir(exist_ok=True)

    host = args.host
    port = _find_open_port(host, args.port)

    print(f"Projects   : {_projects_dir}")
    print(f"Server     : http://{host}:{port}")

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()

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
import csv
import hashlib
import ipaddress
import json
import os
import platform
import re
import secrets
import shutil
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

import pymupdf
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
import uvicorn

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------

# No docs endpoints: this is a single-user local tool, and /docs + /openapi.json
# only widen the surface that a stray local request can enumerate.
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

if getattr(sys, 'frozen', False):
    _base_dir = Path(sys.executable).parent
    _internal_dir = _base_dir / "_internal"
else:
    _base_dir = Path(__file__).parent
    _internal_dir = _base_dir


def _resolve_projects_dir() -> Path:
    """Where this install keeps its projects.

    A packaged copy writes under %LOCALAPPDATA% rather than next to the .exe.
    Two reasons, both about running on someone else's Windows machine: the
    install folder is often not user-writable (Program Files, a read-only
    network share, a managed-software drop), and keeping mutable coder data out
    of the directory holding the executable and its DLLs means routine use
    never needs write access to the code it is executing.

    Overrides, in precedence order:
      TEXT_CODING_DATA_DIR  — explicit path, wins over everything.
      portable.txt          — a marker file next to the .exe, for running from
                              a USB stick or shared drive with the projects
                              travelling alongside the program.
    """
    env_dir = os.environ.get("TEXT_CODING_DATA_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser() / "projects"

    if not getattr(sys, "frozen", False):
        return _base_dir / "projects"

    if (_base_dir / "portable.txt").is_file():
        return _base_dir / "projects"

    local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
    root = Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local"
    return root / "TextCodingProgram" / "projects"


_projects_dir = _resolve_projects_dir()
_static_dir = _internal_dir / "static"

# Session auth — see _local_guard(). Set for real in main()/launch(); the
# module-level default keeps a fresh token in place if the app object is
# imported and served some other way.
_session_token: str = secrets.token_urlsafe(32)
_session_cookie = "tcp_session"
_allowed_hosts: set[str] = set()
_allowed_origins: set[str] = set()
_loopback_only: bool = True

# Louisiana trinomial. Kept as the default because it fits the site-form
# corpora this tool started on, and treated as one configured value of a
# general document id rather than a built-in assumption — a corpus of
# reports, GLO volumes, or conference abstracts sets its own pattern, or
# none at all. Mirrors site_vocab_extractor's item_pattern/trinomial_pattern.
DEFAULT_ITEM_PATTERN = r"(\d{2}[A-Z]{2}\d+)"

_project: dict | None = None
_traits: list[dict] = []
_items: list[str] = []
_segments: dict[str, dict] = {}
_pdf_cache: dict[str, Path] = {}
_page_dpi: int = 150


# ---------------------------------------------------------------------------
# Local-only access control
# ---------------------------------------------------------------------------
#
# This process holds a browser-reachable window onto the coder's filesystem:
# it renders site-form PDFs, writes coded output, and exposes a native
# folder-picker dialog. Binding to 127.0.0.1 keeps it off the network, but
# loopback is not a security boundary on its own — every other program running
# as that user can reach it, and any web page the coder happens to open can
# make their browser issue requests to it. Three checks close that gap:
#
#   1. Session token. Generated per run, handed to the browser once in the URL
#      the launcher opens, then kept in an HttpOnly SameSite=Strict cookie.
#      Another local process cannot read the cookie jar or guess the token, so
#      it cannot drive this server. (Same pattern Jupyter uses.)
#   2. Host header allow-list. Blocks DNS rebinding, where a remote page points
#      a hostname it controls at 127.0.0.1 to get same-origin access.
#   3. Origin check. Blocks cross-site requests outright rather than relying on
#      the JSON content-type requirement to trip up a forged form POST.
#
# Known limitation: cookies are not port-scoped, so a *different* local server
# on another port could be sent this cookie if the browser navigates to it.
# That needs an attacker already running code as this user, which is outside
# what a local single-user tool can defend against; the header path below
# exists so automation can avoid cookies entirely.

_TOKEN_HELP = """Text Coding Program — session token required

Open the program using the link printed in its console window, which looks like:

    http://127.0.0.1:8090/?token=...

That link authorizes this browser for the current session. The token changes
every time the program starts, so an old bookmark will not work — go back to
the console window (or restart the program) to get the current link.
"""


def _is_loopback(addr: str) -> bool:
    try:
        return ipaddress.ip_address(addr).is_loopback
    except ValueError:
        return False


def _set_session_context(host: str, port: int, loopback_only: bool) -> None:
    """Record which Host/Origin values this run will accept."""
    global _allowed_hosts, _allowed_origins, _loopback_only
    _loopback_only = loopback_only
    names = [host, "127.0.0.1", "localhost"] if _is_loopback(host) else [host]
    _allowed_hosts = {f"{n}:{port}" for n in names}
    _allowed_origins = {f"http://{n}:{port}" for n in names}


def session_url(host: str, port: int) -> str:
    return f"http://{host}:{port}/?token={_session_token}"


@app.middleware("http")
async def _local_guard(request, call_next):
    client = request.client.host if request.client else ""
    if _loopback_only and client and not _is_loopback(client):
        return PlainTextResponse("Forbidden: non-local client\n", status_code=403)

    if _allowed_hosts and request.headers.get("host", "") not in _allowed_hosts:
        return PlainTextResponse("Forbidden: unexpected Host header\n", status_code=403)

    origin = request.headers.get("origin")
    if origin is not None and _allowed_origins and origin not in _allowed_origins:
        return PlainTextResponse("Forbidden: cross-origin request\n", status_code=403)

    # A valid token in the query string mints the cookie, then redirects to a
    # clean URL so the token stops riding along in the address bar, in the
    # browser's history, and in any Referer the page later sends.
    query_token = request.query_params.get("token")
    if query_token is not None and secrets.compare_digest(query_token, _session_token):
        response = RedirectResponse(request.url.path or "/", status_code=303)
        response.set_cookie(
            _session_cookie, _session_token,
            httponly=True, samesite="strict", path="/",
        )
        return response

    supplied = request.cookies.get(_session_cookie) or request.headers.get("x-session-token", "")
    if not secrets.compare_digest(supplied, _session_token):
        return PlainTextResponse(_TOKEN_HELP, status_code=401)

    return await call_next(request)


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------

def _load_traits(codebook_dir: Path | None) -> list[dict]:
    # A project whose codebook_dir has moved or been cleared leaves this empty,
    # and Path("") globs the working directory rather than nothing — guard so
    # the project opens with no traits instead of scooping up stray JSON.
    if not codebook_dir or not codebook_dir.is_dir():
        return []
    traits = []
    for p in sorted(codebook_dir.glob("*.json")):
        if p.name.startswith("_") or p.name.startswith("codebook_summary"):
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        if not data.get("full_text"):
            continue
        traits.append(data)
    return traits


def _compile_pattern(pattern: str | None) -> re.Pattern | None:
    """An empty or absent pattern is a real setting: take names as they are.
    Only a non-empty string compiles to a regex."""
    return re.compile(pattern) if pattern else None


def _item_id(name: str, rx: re.Pattern | None) -> str | None:
    """Document id for one file or folder name. With no pattern the name is
    the id, which is what lets a corpus whose names carry no site number —
    papers, abstracts, report volumes — be coded at all. With a pattern, the
    first capture group wins, so a folder named `16VN1000_Smith_1997` still
    resolves to the trinomial its segments file is keyed on."""
    if rx is None:
        return name
    m = rx.search(name)
    return m.group(1) if m else None


def _pdf_stem(path: Path) -> str:
    """Filename without extension and without pdf_ocr's `_ocr` suffix, so
    `Smith_2019_ocr.pdf` and `Smith_2019.pdf` name the same document."""
    stem = path.stem
    return stem[:-4] if stem.endswith("_ocr") else stem


def _discover_items(pdf_dir: Path | None, rx: re.Pattern | None) -> list[tuple[str, Path]]:
    """Every document in the corpus, as (id, source) pairs.

    pdf_ocr writes one subdirectory per document, so subdirectories are read
    first, and `source` is that folder. A corpus that arrives as loose PDFs
    in one folder — the usual shape for papers and abstracts, which have no
    per-document sidecar files to keep together — is read from the filenames
    instead, and `source` is the PDF. The flat pass runs only when the
    subdirectory pass found nothing, so a pdf_ocr output root that happens to
    hold a stray PDF at its top level is unaffected.

    The source path travels with the id because a pattern can shorten a name:
    a folder named `16VN1038_16VN3513` holds one form covering two sites and
    yields the id `16VN1038`, which names no folder on disk. Carrying the
    folder that produced the id is what keeps those documents reachable.
    Where two names collapse onto one id, the first in sorted order wins.
    """
    if not pdf_dir or not pdf_dir.is_dir():
        return []

    def collect(candidates, name_of):
        found: dict[str, Path] = {}
        for c in candidates:
            iid = _item_id(name_of(c), rx)
            if iid and iid not in found:
                found[iid] = c
        return sorted(found.items())

    dirs = collect(sorted(p for p in pdf_dir.iterdir() if p.is_dir()),
                   lambda p: p.name)
    if dirs:
        return dirs
    return collect(sorted(pdf_dir.glob("*.pdf")), _pdf_stem)


def _find_pdf(source: Path, item: str) -> Path | None:
    """The PDF for one document, given the folder or file discovery matched."""
    if source.is_file():
        return source
    if not source.is_dir():
        return None
    for suffix in ("_ocr.pdf", ".pdf"):
        candidate = source / f"{item}{suffix}"
        if candidate.exists():
            return candidate
    for f in sorted(source.glob("*_ocr.pdf")):
        return f
    for f in sorted(source.glob("*.pdf")):
        return f
    return None


# Segmenters name the document id differently: site forms write `trinomial`
# (segmenter.py), the report pass-0 writes `report`
# (segment_reports_pass0.py), and both GLO passes write `document`. Reading
# all of them is what site_form_segmenter's own generate_inventory.py does.
_SEGMENT_ID_KEYS = ("trinomial", "report", "document", "item")


def _segments_file_id(data: dict, path: Path, rx: re.Pattern | None) -> str:
    """Document id for one segments.json, from its own id field when it has
    one, otherwise from its filename.

    The filename fallback strips the `.segments` suffix and any
    `<model_slug>__` prefix that run-folder output carries, the same layout
    site_vocab_extractor's `_find_segments_file` resolves. Without that
    stripping a run-folder file lands under a key like
    `qwen2_5_14b__volume_12.segments`, which matches no discovered document
    and leaves the project silently unsegmented."""
    for key in _SEGMENT_ID_KEYS:
        val = data.get(key)
        if val:
            return str(val)
    stem = path.stem
    if stem.endswith(".segments"):
        stem = stem[: -len(".segments")]
    if "__" in stem:
        stem = stem.split("__", 1)[1]
    return _item_id(stem, rx) or stem


def _load_segments(segments_dir: Path, rx: re.Pattern | None) -> dict[str, dict]:
    if not segments_dir or not segments_dir.is_dir():
        return {}
    segs = {}
    for p in sorted(segments_dir.glob("*.segments.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        segs[_segments_file_id(data, p, rx)] = data
    return segs


def _segment_key(seg: dict, fallback_index: int) -> str:
    """Stable key for deduping/matching one trinomial's segments.

    A segment's human-authored label (site_form_segmenter's free-text
    `label`/`segment_label` field) is used directly when present — this
    keeps every already-saved file's keys unchanged. Labels are not
    guaranteed unique or even present, though (a segment can legitimately
    have no label, and nothing enforces distinct labels across segments in
    one segments.json) — so when the label is empty, fall back to a
    position-based key instead of collapsing every unlabeled segment onto
    the same 'all' bucket, which previously caused unrelated segments for
    the same trinomial to silently overwrite each other's coded traits.
    `segment_index`, when present on the dict, is preferred over
    `fallback_index` since it reflects the segment's live position in
    segments.json rather than wherever it happens to sit in a persisted
    file.
    """
    label = seg.get("segment_label") or seg.get("label")
    if label:
        return label
    idx = seg.get("segment_index")
    if idx is None:
        idx = fallback_index
    return f"__unlabeled_{idx}__"


def _coded_dir() -> Path:
    d = Path(_project["project_dir"]) / "coded"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _coded_path(tri: str) -> Path:
    """Resolve a trinomial to its output file, refusing anything not in the
    loaded project's own discovered set. The trinomial arrives as a URL path
    segment and is interpolated into a filename, so accepting it on faith would
    let a crafted request steer the write anywhere the user can write. Checking
    membership rather than sanitizing the string keeps this closed by
    construction — the only reachable names are ones the project discovered."""
    if tri not in _pdf_cache:
        raise HTTPException(404, f"Trinomial not found: {tri}")
    path = (_coded_dir() / f"{tri}.coded.json").resolve()
    if path.parent != _coded_dir().resolve():
        raise HTTPException(400, f"Invalid trinomial: {tri}")
    return path


def _load_project_data(proj: dict) -> None:
    """Load traits, documents, segments, PDFs for the given project."""
    global _project, _traits, _items, _segments, _pdf_cache, _page_dpi

    _project = proj
    # item_pattern is the current name; trinomial_pattern is what projects
    # created before the rename carry, and still works. A project that sets
    # item_pattern to an empty string means that deliberately, so `in` rather
    # than `or` decides which key wins.
    if "item_pattern" in proj:
        pattern = proj["item_pattern"]
    else:
        pattern = proj.get("trinomial_pattern", DEFAULT_ITEM_PATTERN)
    rx = _compile_pattern(pattern)
    _page_dpi = proj.get("page_dpi", 150)

    # Empty is a real state, not a broken one — a project can outlive the
    # directory it pointed at. Path("") is Path("."), which would silently scan
    # the working directory, so keep it None instead.
    pdf_dir = Path(proj["pdf_dir"]) if proj.get("pdf_dir") else None
    codebook_dir = Path(proj["codebook_dir"]) if proj.get("codebook_dir") else None

    _traits = _load_traits(codebook_dir)
    discovered = _discover_items(pdf_dir, rx)
    _items = [item for item, _ in discovered]

    _pdf_cache.clear()
    for item, source in discovered:
        pdf = _find_pdf(source, item)
        if pdf:
            _pdf_cache[item] = pdf

    seg_dir = proj.get("segments_dir")
    _segments = _load_segments(Path(seg_dir), rx) if seg_dir else {}

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

    segments_dir_raw = body.get("segments_dir", "").strip()
    if segments_dir_raw:
        seg_p = Path(segments_dir_raw)
        if not seg_p.is_dir():
            raise HTTPException(400, f"segments_dir not found: {seg_p}")
        if not any(seg_p.glob("*.segments.json")):
            raise HTTPException(
                400,
                f"segments_dir has no *.segments.json files directly in it: {seg_p}\n"
                "site_form_segmenter writes these one level deeper, inside a "
                "run folder (runs/<timestamp>_<gitsha>/) — point segments_dir "
                "at that run folder itself, not its parent. Proceeding without "
                "fixing this would silently create a project with no segments."
            )

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
        "item_pattern": body.get("item_pattern",
                                 body.get("trinomial_pattern", DEFAULT_ITEM_PATTERN)),
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

    return {"status": "loaded", "name": proj["name"], "documents": len(_items),
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
    for tri in _items:
        if tri not in _pdf_cache:
            continue
        seg_data = _segments.get(tri)
        if seg_data and seg_data.get("segments"):
            for i, seg in enumerate(seg_data["segments"]):
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
                    "segment_index": i,
                    "segment_key": _segment_key(seg, i),
                    "pages": sorted(seg.get("pages", [])),
                    "page_groups": page_groups,
                })
        else:
            work_queue.append({
                "trinomial": tri,
                "segment_label": None,
                "segment_year": None,
                "segment_index": None,
                "segment_key": "all",
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
    out_path = _coded_path(tri)

    seg_data = _segments.get(tri)
    segment_defs = seg_data.get("segments", []) if seg_data else []

    pdf_path = _pdf_cache[tri]
    doc = pymupdf.open(str(pdf_path))
    n_pages = len(doc)
    doc.close()

    coded_traits: dict[str, dict] = {}
    if out_path.exists():
        existing = json.loads(out_path.read_text(encoding="utf-8"))
        for i, seg in enumerate(existing.get("segments", [])):
            seg_key = _segment_key(seg, i)
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
    out_path = _coded_path(tri)

    existing = None
    if out_path.exists():
        existing = json.loads(out_path.read_text(encoding="utf-8"))

    now = datetime.now(timezone.utc).isoformat()

    # Merge incoming segments into existing ones by a stable per-segment key
    # rather than replacing the whole array — the client only ever
    # round-trips the segment(s) currently in view, so a naive overwrite
    # would silently discard previously-saved segments for this trinomial.
    # The key prefers segment_label (works the same whether a segment is a
    # site investigation, a report structural section, or a narrowed
    # per-trinomial pass — nothing here assumes what kind of segment it is)
    # but falls back to a position-based key when the label is empty, so
    # multiple unlabeled segments for one trinomial no longer collide onto
    # the same key and silently overwrite each other's traits.
    by_key: dict = {}
    if existing:
        for i, seg in enumerate(existing.get("segments", [])):
            by_key[_segment_key(seg, i)] = seg
    for i, seg in enumerate(body.get("segments", [])):
        by_key[_segment_key(seg, i)] = seg

    result = {
        "trinomial": tri,
        "segments": list(by_key.values()),
        "coder_id": _project["coder_id"],
        "project": _project["name"],
        "first_saved": existing["first_saved"] if existing else now,
        "last_saved": now,
    }

    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"status": "saved", "path": str(out_path)}


# ---------------------------------------------------------------------------
# API — Export a project
# ---------------------------------------------------------------------------
#
# An export carries the coded results, the metadata needed to interpret them,
# and nothing else. Two things are deliberately absent: the source PDFs and the
# segment map. Site forms carry protected locational data and the OCR corpus
# runs to gigabytes, so an export is meant to be a small artifact you can move
# to another computer and read — not a second copy of the source material.
#
# The results appear in three forms, because they answer different needs:
#
#   coded_data.csv       flat, one row per coded trait — opens in Excel/R/pandas
#                        and merges with site_coder output for IRR without any
#                        preprocessing. This is the "look at the results" file.
#   project_export.json  everything in one structured document, for scripts that
#                        want the nesting the CSV flattens away.
#   coded/               the per-trinomial .coded.json files, byte-for-byte, so
#                        the export is never a lossy re-encoding of the originals.
#
# The codebook travels alongside them so a coded value stays interpretable
# without the codebook repo on hand.

_EXPORT_FORMAT = "text_coding_program_export"
_EXPORT_FORMAT_VERSION = 2

# Keep in sync with build.py's VERSION and pyproject.toml.
PROGRAM_VERSION = "0.1.0"

_CSV_COLUMNS = [
    "trinomial", "segment_key", "segment_label", "segment_year",
    "trait_key", "trait_title", "trait_value", "confidence",
    "justification", "evidence_pages",
    "coder_id", "project", "first_saved", "last_saved",
]


def _csv_scalar(value) -> str:
    """Render one stored trait value as a single CSV cell.

    trait_value is not one type: binary traits store a bool, numeric/free-text
    traits a string, and multi-select list traits an array. Lists are joined
    with '; ' rather than ',' so the cell stays readable in Excel instead of
    looking like it should have been more columns.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return "; ".join(_csv_scalar(v) for v in value)
    return str(value)


def _coded_files(proj: dict) -> list[Path]:
    coded = Path(proj["project_dir"]) / "coded"
    return sorted(coded.glob("*.coded.json")) if coded.is_dir() else []


def _trait_titles(codebook_dir: Path) -> dict[str, str]:
    """code_id -> human title, for a readable CSV. Empty when the codebook is
    unreachable — a moved codebook should degrade the export, not fail it."""
    if not codebook_dir or not codebook_dir.is_dir():
        return {}
    try:
        return {t["code_id"]: t.get("title", "") for t in _load_traits(codebook_dir)}
    except Exception:
        return {}


def _build_export_rows(proj: dict, titles: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """Return (csv_rows, coded_documents) for one project."""
    rows: list[dict] = []
    docs: list[dict] = []

    for path in _coded_files(proj):
        data = json.loads(path.read_text(encoding="utf-8"))
        docs.append(data)
        tri = data.get("trinomial") or path.name.removesuffix(".coded.json")
        for i, seg in enumerate(data.get("segments", [])):
            for tr in seg.get("traits", []):
                rows.append({
                    "trinomial": tri,
                    "segment_key": _segment_key(seg, i),
                    "segment_label": seg.get("segment_label") or seg.get("label") or "",
                    "segment_year": seg.get("segment_year") or seg.get("year") or "",
                    "trait_key": tr.get("trait_key", ""),
                    "trait_title": titles.get(tr.get("trait_key", ""), ""),
                    "trait_value": _csv_scalar(tr.get("trait_value")),
                    "confidence": _csv_scalar(tr.get("confidence")),
                    "justification": tr.get("justification") or "",
                    "evidence_pages": _csv_scalar(tr.get("evidence_pages")),
                    "coder_id": data.get("coder_id", ""),
                    "project": data.get("project", ""),
                    "first_saved": data.get("first_saved", ""),
                    "last_saved": data.get("last_saved", ""),
                })

    rows.sort(key=lambda r: (r["trinomial"], r["segment_key"], r["trait_key"]))
    return rows, docs


def _copy_glob(src: Path, dest: Path, patterns: tuple[str, ...]) -> int:
    n = 0
    for pattern in patterns:
        for f in sorted(src.glob(pattern)):
            if f.is_file():
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest / f.name)
                n += 1
    return n


def _dir_size(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _export_project(proj: dict, dest_root: Path) -> dict:
    """Write the export folder. Returns a summary for the UI."""
    # Seconds, plus a suffix if that still collides. Exporting the same project
    # twice in quick succession is normal — once results-only to mail, once with
    # PDFs to archive — so a name clash should never be the user's problem.
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = dest_root / f"{proj['slug']}_export_{stamp}"
    n = 2
    while out.exists():
        out = dest_root / f"{proj['slug']}_export_{stamp}_{n}"
        n += 1
    out.mkdir(parents=True)

    warnings: list[str] = []
    codebook_dir = Path(proj["codebook_dir"]) if proj.get("codebook_dir") else None
    titles = _trait_titles(codebook_dir) if codebook_dir else {}
    rows, docs = _build_export_rows(proj, titles)

    # --- results, in all three shapes ---
    with (out / "coded_data.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    coded_src = Path(proj["project_dir"]) / "coded"
    if coded_src.is_dir():
        _copy_glob(coded_src, out / "coded", ("*.coded.json",))

    # --- the codebook, so a coded value stays interpretable on its own ---
    codebook_files = 0
    if codebook_dir and codebook_dir.is_dir():
        codebook_files = _copy_glob(codebook_dir, out / "codebook", ("*.json",))
    else:
        warnings.append(
            f"Codebook directory not found, so no definitions were included: "
            f"{proj.get('codebook_dir')}. The coded results are complete, but "
            "trait_title is blank in the CSV."
        )

    # --- metadata ---
    # Source paths are recorded as provenance, under the export block rather
    # than at the top level: they describe where this project's inputs lived on
    # the machine that made the export, and nothing here resolves them.
    export_proj = {k: v for k, v in proj.items()
                   if k not in ("project_dir", "pdf_dir", "codebook_dir", "segments_dir")}
    export_proj["export"] = {
        "format": _EXPORT_FORMAT,
        "format_version": _EXPORT_FORMAT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "exported_from": {
            "machine": platform.node(),
            "user": os.environ.get("USERNAME") or os.environ.get("USER") or "",
            "program_version": PROGRAM_VERSION,
        },
        "source_paths": {
            "pdf_dir": proj.get("pdf_dir", ""),
            "codebook_dir": proj.get("codebook_dir", ""),
            "segments_dir": (proj.get("segments_dir") or "").strip(),
        },
        "contents": {
            "coded_results": True,
            "codebook_files": codebook_files,
            "pdfs": False,
            "segments": False,
        },
    }
    (out / "project.json").write_text(
        json.dumps(export_proj, indent=2, ensure_ascii=False), encoding="utf-8")

    (out / "project_export.json").write_text(json.dumps({
        "project": export_proj,
        "coded": docs,
        "coded_rows": rows,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    summary = {
        "export_dir": str(out),
        "trinomials": len(docs),
        "trait_rows": len(rows),
        "codebook_files": codebook_files,
        "size_mb": round(_dir_size(out) / (1024 * 1024), 1),
        "warnings": warnings,
    }

    manifest = dict(summary)
    manifest["contents"] = export_proj["export"]["contents"]
    manifest["sha256"] = {
        name: hashlib.sha256((out / name).read_bytes()).hexdigest()
        for name in ("coded_data.csv", "project_export.json")
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    _write_export_readme(out, export_proj, summary)
    return summary


def _write_export_readme(out: Path, proj: dict, summary: dict) -> None:
    warn_block = ""
    if summary["warnings"]:
        warn_block = "\nWARNINGS FROM THIS EXPORT\n" + "-" * 25 + "\n" + \
            "\n".join(f"* {w}" for w in summary["warnings"]) + "\n"

    codebook_line = (
        f"Codebook definitions, {summary['codebook_files']} trait file(s)."
        if summary["codebook_files"] else "NOT INCLUDED — see warnings above."
    )

    (out / "README.txt").write_text(f"""Text Coding Program — exported results
======================================

Project : {proj.get('name', '')}
Coder   : {proj.get('coder_id', '')}
Exported: {proj['export']['exported_at']} from {proj['export']['exported_from']['machine']}

Contents: {summary['trinomials']} coded trinomial(s), {summary['trait_rows']} trait entries, {summary['size_mb']} MB.
{warn_block}
START HERE
----------
Open coded_data.csv. Nothing else is needed and the program does not have to be
installed anywhere.

WHAT IS IN HERE
---------------
coded_data.csv       One row per coded trait — the file to open in Excel, R, or
                     pandas. Written UTF-8 with a BOM so Excel gets the encoding
                     right on a double-click. trinomial + segment_key +
                     trait_key identify a row, which is the shape an inter-rater
                     comparison against site_coder output needs. Multi-select
                     values and evidence_pages are joined with '; ' inside one
                     cell. Page numbers are exactly as the viewer recorded them,
                     not renumbered.
project_export.json  The same data structured rather than flattened, plus the
                     project metadata, in one document.
coded/               The original per-trinomial .coded.json files, unmodified.
codebook/            {codebook_line}
project.json         Project metadata: name, coder, and where the source files
                     lived on the machine that made this export.
manifest.json        Counts, contents, and SHA-256 of the CSV and JSON.

WHAT IS NOT IN HERE
-------------------
No source PDFs and no segment map, by design. This folder holds coded results
and the definitions needed to read them — it is not a copy of the site forms,
and it cannot be used to reopen the project for further coding.

Because of that, the evidence_pages numbers refer to pages of documents that
are not in this folder. They are here so a coded value can be traced back on a
machine that does have the source corpus.
""", encoding="utf-8")


@app.post("/api/projects/export")
async def export_project_endpoint(body: dict):
    slug = (body.get("slug") or "").strip()
    proj_path = _projects_dir / slug / "project.json"
    if not slug or not proj_path.is_file():
        raise HTTPException(404, f"Project not found: {slug}")

    dest_raw = (body.get("dest_dir") or "").strip()
    if not dest_raw:
        raise HTTPException(400, "Choose a destination folder for the export")
    dest_root = Path(dest_raw)
    if not dest_root.is_dir():
        raise HTTPException(400, f"Destination folder not found: {dest_root}")

    proj = json.loads(proj_path.read_text(encoding="utf-8"))
    proj["project_dir"] = str(_projects_dir / slug)
    return _export_project(proj, dest_root)


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
    ap.add_argument(
        "--allow-non-loopback", action="store_true",
        help="Permit binding to a non-loopback address. Off by default: this "
             "server renders site PDFs and exposes a filesystem folder picker, "
             "so reaching it from the network means anyone who can route to "
             "this machine can read the documents being coded.",
    )
    args = ap.parse_args()

    host = args.host
    if not _is_loopback(host) and not args.allow_non_loopback:
        sys.exit(
            f"Refusing to bind {host}: that exposes the PDFs being coded and "
            "the folder-picker endpoint beyond this machine.\n"
            "Use --host 127.0.0.1, or pass --allow-non-loopback if you have a "
            "reason to serve it on the network."
        )

    _projects_dir.mkdir(parents=True, exist_ok=True)

    port = _find_open_port(host, args.port)
    _set_session_context(host, port, loopback_only=not args.allow_non_loopback)

    print(f"Projects   : {_projects_dir}")
    print(f"Open this  : {session_url(host, port)}")
    print("             (the token authorizes your browser; it changes each run)")

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()

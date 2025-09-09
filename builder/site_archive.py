import io
import json
import os
import re
import tempfile
import zipfile
from typing import Any, Dict, Iterable, List, Set

import frappe
from frappe.utils import get_url, now_datetime
from frappe.utils.file_manager import save_file


ARCHIVE_FORMAT = "builder-site-archive"
ARCHIVE_VERSION = 1


def export_site(pages: List[str] | None = None, include_drafts: bool = False, with_assets: bool = True) -> Dict[str, Any]:
    """Export Builder Pages and referenced File assets into a single ZIP.

    Args:
        pages: Optional list of Builder Page names or routes to export. If None, exports all published pages
               (and drafts if include_drafts is True).
        include_drafts: Include `draft_blocks` JSON in serialization and scan for assets in it.
        with_assets: Whether to collect and bundle File binaries referenced from pages.

    Returns:
        Dict with keys: url, filename
    """
    _ensure_permission()

    page_names = _resolve_pages(pages, include_drafts)

    tmpdir = tempfile.mkdtemp(prefix="builder-export-")
    pages_dir = os.path.join(tmpdir, "pages")
    assets_dir = os.path.join(tmpdir, "assets")
    os.makedirs(pages_dir, exist_ok=True)
    os.makedirs(assets_dir, exist_ok=True)

    manifest: Dict[str, Any] = {
        "format": ARCHIVE_FORMAT,
        "format_version": ARCHIVE_VERSION,
        "exported_at": now_datetime().isoformat(),
        "builder_version": frappe.__version__,
        "site": {"name": frappe.local.site, "base_url": get_url()},
        "counts": {"pages": 0, "assets": 0},
        "pages": [],
        "assets": [],
        "mappings": {"file_id_to_archive": {}},
    }

    collected_file_urls: Set[str] = set()

    for name in page_names:
        doc = frappe.get_doc("Builder Page", name)
        payload = _serialize_page(doc, include_drafts=include_drafts)
        # normalize JSON and compute checksum
        serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        checksum = _sha256(serialized.encode())
        page_slug = payload.get("route") or doc.name
        with open(os.path.join(pages_dir, f"{page_slug}.json"), "w", encoding="utf-8") as f:
            f.write(serialized)
        manifest["pages"].append(
            {
                "slug": page_slug,
                "doctype": doc.doctype,
                "title": doc.page_title,
                "id": doc.name,
                "checksum": f"sha256:{checksum}",
            }
        )

        # collect assets from page-level fields and blocks
        collected_file_urls |= _collect_page_asset_urls(payload)

    if with_assets and collected_file_urls:
        for url in sorted(collected_file_urls):
            file_doc = _get_file_by_url(url)
            if not file_doc:
                # Skip external or unresolved URLs
                continue
            content = file_doc.get_content()
            sha = _sha256(content)
            stored = f"{sha[:8]}-{os.path.basename(file_doc.file_name)}"
            with open(os.path.join(assets_dir, stored), "wb") as fh:
                fh.write(content)
            # resolve mime type from available attributes or filename
            try:
                import mimetypes
            except Exception:  # pragma: no cover
                mimetypes = None
            mime = getattr(file_doc, "mime_type", None) or getattr(file_doc, "file_type", None)
            if not mime and mimetypes:
                mime = mimetypes.guess_type(file_doc.file_name)[0]
            manifest["assets"].append(
                {
                    "file_id": file_doc.name,
                    "orig_name": file_doc.file_name,
                    "stored_name": stored,
                    "sha256": sha,
                    "mime": mime,
                    "file_url": file_doc.file_url,
                    "is_private": int(getattr(file_doc, "is_private", 0) or 0),
                }
            )
            manifest["mappings"]["file_id_to_archive"][file_doc.name] = f"assets/{stored}"

    manifest["counts"] = {"pages": len(manifest["pages"]), "assets": len(manifest["assets"])}
    with open(os.path.join(tmpdir, "manifest.json"), "w", encoding="utf-8") as mf:
        json.dump(manifest, mf, indent=2, ensure_ascii=False)

    # zip folder
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(tmpdir):
            for f in files:
                full = os.path.join(root, f)
                arcname = os.path.relpath(full, tmpdir)
                z.write(full, arcname)

    fname = f"site-export-{now_datetime().strftime('%Y%m%d-%H%M%S')}.zip"
    file_rec = save_file(fname, buf.getvalue(), None, None, is_private=1)
    return {"url": file_rec.file_url, "filename": fname}


def import_site(archive: str | Dict[str, Any], mode: str = "upsert") -> Dict[str, Any]:
    """Import a Builder Site archive created by export_site.

    Args:
        archive: File URL ("/private/files/..." or "/files/...") or File name (Docname)
        mode: "upsert" | "create_only" | "overwrite"

    Returns:
        Dict with summary and remap tables
    """
    _ensure_permission()

    file_doc = _resolve_archive_file(archive)
    content = file_doc.get_content()

    with tempfile.TemporaryDirectory(prefix="builder-import-") as tmp:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            _reject_unsafe_members(z)
            z.extractall(tmp)

        manifest_path = os.path.join(tmp, "manifest.json")
        if not os.path.exists(manifest_path):
            frappe.throw("Invalid archive: missing manifest.json")
        with open(manifest_path, encoding="utf-8") as mf:
            manifest = json.load(mf)
        _validate_manifest(manifest)

        # validate checksums
        for p in manifest.get("pages", []):
            path = os.path.join(tmp, "pages", f"{p['slug']}.json")
            with open(path, encoding="utf-8") as fh:
                data = fh.read()
            checksum = _sha256(data.encode())
            if f"sha256:{checksum}" != p.get("checksum"):
                frappe.throw(f"Checksum mismatch for page {p['slug']}")

        for a in manifest.get("assets", []):
            path = os.path.join(tmp, "assets", a["stored_name"]) if a.get("stored_name") else None
            if path and os.path.exists(path):
                with open(path, "rb") as fh:
                    data = fh.read()
                sha = _sha256(data)
                if sha != a.get("sha256"):
                    frappe.throw(f"Checksum mismatch for asset {a.get('orig_name')}")

        # import files first, build remap tables
        old_file_id_to_new: Dict[str, str] = {}
        old_url_to_new_url: Dict[str, str] = {}
        for a in manifest.get("assets", []):
            path = os.path.join(tmp, "assets", a["stored_name"]) if a.get("stored_name") else None
            if not path or not os.path.exists(path):
                continue
            with open(path, "rb") as fh:
                data = fh.read()
            # save_file dedupes by content hash + privacy
            is_private = int(a.get("is_private") or 0)
            new_file = save_file(a.get("orig_name"), data, None, None, is_private=is_private)
            old_file_id_to_new[a.get("file_id")] = new_file.name
            if a.get("file_url"):
                old_url_to_new_url[a.get("file_url")] = new_file.file_url

        results = {"created": [], "updated": [], "skipped": [], "errors": []}
        for p in manifest.get("pages", []):
            slug = p["slug"]
            page_path = os.path.join(tmp, "pages", f"{slug}.json")
            try:
                with open(page_path, encoding="utf-8") as fh:
                    payload = json.load(fh)
            except Exception as e:
                results["errors"].append({"page": slug, "error": str(e)})
                continue

            # remap file references in JSON
            payload = _remap_urls_in_payload(payload, old_url_to_new_url)

            # find existing page by route, else by name
            existing = _find_existing_page(payload)

            if existing:
                if mode == "create_only":
                    results["skipped"].append(slug)
                    continue
                doc = frappe.get_doc("Builder Page", existing)
                _apply_payload(doc, payload, overwrite=(mode == "overwrite"))
                doc.save(ignore_permissions=True)
                results["updated"].append(slug)
            else:
                doc = frappe.new_doc("Builder Page")
                _apply_payload(doc, payload, overwrite=True)
                doc.insert(ignore_permissions=True)
                results["created"].append(slug)

        frappe.enqueue("frappe.website.doctype.website_settings.website_settings.rebuild_website")
        return {"summary": results, "file_map": old_file_id_to_new, "url_map": old_url_to_new_url}


# --------------------- helpers ---------------------


def _ensure_permission() -> None:
    # Allow if user has Builder Manager OR has at least read access to Builder Page
    try:
        frappe.only_for(("Builder Manager",))
        return
    except Exception:
        pass

    if frappe.session.user and frappe.session.user != "Guest":
        if frappe.has_permission("Builder Page", ptype="write") or frappe.has_permission(
            "Builder Page", ptype="read"
        ):
            return

    frappe.throw("Not permitted", frappe.PermissionError)


def _resolve_pages(pages: List[str] | None, include_drafts: bool) -> List[str]:
    if pages:
        # Pages may be provided as routes; convert to names where possible
        names = []
        for p in pages:
            name = frappe.db.get_value("Builder Page", {"name": p}, "name") or frappe.db.get_value(
                "Builder Page", {"route": p}, "name"
            )
            if name:
                names.append(name)
        return names

    filters = {}
    if not include_drafts:
        filters["published"] = 1
    return frappe.get_all("Builder Page", filters=filters, pluck="name")


def _serialize_page(doc, *, include_drafts: bool) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "name": doc.name,
        "doctype": doc.doctype,
        "page_title": doc.page_title,
        "route": doc.route,
        "published": int(doc.published or 0),
        "disable_indexing": int(getattr(doc, "disable_indexing", 0) or 0),
        "authenticated_access": int(getattr(doc, "authenticated_access", 0) or 0),
        "dynamic_route": int(getattr(doc, "dynamic_route", 0) or 0),
        "blocks": frappe.parse_json(doc.blocks or "[]"),
        "favicon": getattr(doc, "favicon", None),
        "meta_image": getattr(doc, "meta_image", None),
        "head_html": getattr(doc, "head_html", None),
        "body_html": getattr(doc, "body_html", None),
        "page_data_script": getattr(doc, "page_data_script", None),
    }
    if include_drafts and getattr(doc, "draft_blocks", None):
        payload["draft_blocks"] = frappe.parse_json(doc.draft_blocks)
    return payload


def _collect_page_asset_urls(payload: Dict[str, Any]) -> Set[str]:
    urls: Set[str] = set()
    site_url = get_url()

    def consider(url: str | None):
        if not url or not isinstance(url, str):
            return
        if url.startswith(site_url):
            url = url.split(site_url, 1)[1]
        # strip query string
        url = url.split("?", 1)[0]
        if url.startswith("/files/") or url.startswith("/private/files/"):
            urls.add(url)

    # page-level images
    consider(payload.get("favicon"))
    consider(payload.get("meta_image"))

    # from blocks
    def walk(obj: Any):
        if isinstance(obj, dict):
            el = obj.get("element")
            if el == "img":
                src = (obj.get("attributes") or {}).get("src")
                consider(src)
            # style attribute might contain url(...)
            style_attr = (obj.get("attributes") or {}).get("style")
            if isinstance(style_attr, str):
                for u in _extract_urls_from_style(style_attr):
                    consider(u)
            # baseStyles/rawStyles may contain backgroundImage, etc.
            for style_map_key in ("baseStyles", "mobileStyles", "tabletStyles", "rawStyles"):
                style_map = obj.get(style_map_key) or {}
                if isinstance(style_map, dict):
                    for v in style_map.values():
                        if isinstance(v, str):
                            for u in _extract_urls_from_style(v):
                                consider(u)
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(payload.get("blocks"))
    if payload.get("draft_blocks"):
        walk(payload.get("draft_blocks"))
    return urls


_URL_IN_STYLE_RE = re.compile(r"url\((?:'|\")?(?P<url>[^)\'\"]+)(?:'|\")?\)")


def _extract_urls_from_style(style_value: str) -> Iterable[str]:
    for m in _URL_IN_STYLE_RE.finditer(style_value or ""):
        yield m.group("url")


def _get_file_by_url(url: str):
    # Incoming may include query string removed by caller
    url = (url or "").split("?", 1)[0]
    name = frappe.db.get_value("File", {"file_url": url}, "name")
    return frappe.get_doc("File", name) if name else None


def _sha256(b: bytes) -> str:
    import hashlib

    return hashlib.sha256(b if isinstance(b, (bytes, bytearray)) else b.encode()).hexdigest()


def _reject_unsafe_members(z: zipfile.ZipFile) -> None:
    for m in z.namelist():
        if m.startswith("/"):
            frappe.throw("Invalid archive paths")
        norm = os.path.normpath(m)
        if ".." in norm.split(os.sep):
            frappe.throw("Invalid archive paths")


def _validate_manifest(m: Dict[str, Any]) -> None:
    if m.get("format") != ARCHIVE_FORMAT:
        frappe.throw("Invalid archive format")
    if m.get("format_version") not in (ARCHIVE_VERSION,):
        frappe.throw("Unsupported archive version")


def _resolve_archive_file(archive: str | Dict[str, Any]):
    if isinstance(archive, str):
        if archive.startswith("/"):
            name = frappe.db.get_value("File", {"file_url": archive}, "name")
            if not name:
                frappe.throw("Archive file not found")
            return frappe.get_doc("File", name)
        return frappe.get_doc("File", archive)
    if isinstance(archive, dict) and archive.get("name"):
        return frappe.get_doc("File", archive.get("name"))
    frappe.throw("Invalid archive reference")


def _remap_urls_in_payload(payload: Dict[str, Any], url_map: Dict[str, str]) -> Dict[str, Any]:
    site_url = get_url()

    def remap_url(u: str | None) -> str | None:
        if not u or not isinstance(u, str):
            return u
        # normalize with site prefix removed and query stripped
        local = u
        if local.startswith(site_url):
            local = local.split(site_url, 1)[1]
        local = local.split("?", 1)[0]
        return url_map.get(local) or url_map.get(u) or u

    # page-level
    payload["favicon"] = remap_url(payload.get("favicon"))
    payload["meta_image"] = remap_url(payload.get("meta_image"))

    def process_node(node: Dict[str, Any]) -> None:
        # remap <img src>
        if node.get("element") == "img":
            attrs = node.get("attributes") or {}
            attrs["src"] = remap_url(attrs.get("src"))
            node["attributes"] = attrs
        # attributes.style
        attrs = node.get("attributes")
        if isinstance(attrs, dict) and isinstance(attrs.get("style"), str):
            attrs["style"] = _remap_in_style(attrs["style"], remap_url)
        # style maps
        for style_map_key in ("baseStyles", "mobileStyles", "tabletStyles", "rawStyles"):
            style_map = node.get(style_map_key)
            if isinstance(style_map, dict):
                for k, v in list(style_map.items()):
                    if isinstance(v, str):
                        style_map[k] = _remap_in_style(v, remap_url)

    # Non-recursive traversal with cycle guards
    seen: Set[int] = set()
    stack: List[Any] = []
    if payload.get("blocks") is not None:
        stack.append(payload.get("blocks"))
    if payload.get("draft_blocks") is not None:
        stack.append(payload.get("draft_blocks"))

    processed = 0
    max_nodes = 200000  # safety guard

    while stack:
        obj = stack.pop()
        oid = id(obj)
        if oid in seen:
            continue
        seen.add(oid)
        processed += 1
        if processed > max_nodes:
            break
        if isinstance(obj, dict):
            process_node(obj)
            for v in obj.values():
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(obj, list):
            for v in obj:
                if isinstance(v, (dict, list)):
                    stack.append(v)

    return payload


def _remap_in_style(style_value: str, remap_fn) -> str:
    def repl(match: re.Match) -> str:
        u = match.group("url")
        new_u = remap_fn(u)
        return match.group(0).replace(u, new_u)

    return _URL_IN_STYLE_RE.sub(repl, style_value or "")


def _find_existing_page(payload: Dict[str, Any]) -> str | None:
    if payload.get("route"):
        name = frappe.db.get_value("Builder Page", {"route": payload.get("route")}, "name")
        if name:
            return name
    if payload.get("name"):
        if frappe.db.exists("Builder Page", payload.get("name")):
            return payload.get("name")
    return None


def _apply_payload(doc, payload: Dict[str, Any], *, overwrite: bool) -> None:
    # Controlled set of fields
    fields = [
        "page_title",
        "route",
        "published",
        "disable_indexing",
        "authenticated_access",
        "dynamic_route",
        "favicon",
        "meta_image",
        "head_html",
        "body_html",
        "page_data_script",
    ]
    for f in fields:
        v = payload.get(f)
        if overwrite or v is not None:
            setattr(doc, f, v)

    # blocks
    doc.blocks = frappe.as_json(payload.get("blocks") or [], indent=None)
    if hasattr(doc, "draft_blocks"):
        draft = payload.get("draft_blocks")
        doc.draft_blocks = frappe.as_json(draft, indent=None) if draft else None

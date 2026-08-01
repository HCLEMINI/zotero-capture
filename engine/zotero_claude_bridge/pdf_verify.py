"""PDF ground-truth verification (Problem 3/4/6).

`pdf_status:"ok"` from the extension is NOT reliable:
  - it only flips to "failed" when an attachment explicitly errors out;
  - if the translator never produced a PDF attachment, it still reports "ok";
  - attachment keys are never exposed in the CaptureResult.
The only reliable ground truth is the Zotero storage file itself: read its first
4 bytes and check for the `%PDF` magic header.

zotero.sqlite is opened read-only (mode=ro&immutable=1) so a running Zotero client
is not disturbed. Resolution chain:
    parent item key -> itemAttachments(contentType='application/pdf')
    -> attachment key -> storage/<att_key>/<filename> -> read %PDF magic.

When the parent key is absent from the CaptureResult (a known gap in the modern
itemSaver path, itemSaver.js:195), fall back to a title LIKE match.
"""
import os
import re
import sqlite3
from typing import Any, Optional

from .schemas import VERIFY_ALL_HARD_MAX


def _connect_ro(sqlite_path: str):
    """Open zotero.sqlite read-only so a running Zotero is not disturbed."""
    return sqlite3.connect(f"file:{sqlite_path}?mode=ro&immutable=1", uri=True)


def _strip_storage_prefix(path: Optional[str]) -> Optional[str]:
    """`storage:filename.pdf` -> `filename.pdf`."""
    if not path:
        return None
    return path.split(":", 1)[1] if path.lower().startswith("storage:") else path


def _attachment_for_parent(con, parent_key: str):
    """Return (attachment_key, filename) for the PDF child of parent_key, or None."""
    cur = con.execute(
        """SELECT a.key, ia.path FROM items p
           JOIN itemAttachments ia ON ia.parentItemID = p.itemID
           JOIN items a ON a.itemID = ia.itemID
           WHERE p.key = ? AND ia.contentType = 'application/pdf'""",
        (parent_key,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return row[0], _strip_storage_prefix(row[1])


def _parent_keys_by_title(con, title: str):
    """Fallback when the parent key is absent: match items by title (LIKE).

    Zotero stores field values in itemData/itemDataValues (joined via fields), not
    as a column on the items table, so we join through that.
    """
    if not title:
        return []
    pat = re.sub(r"([%_\\])", r"\\\1", title).strip()  # escape LIKE wildcards
    if not pat:
        return []
    cur = con.execute(
        """SELECT i.key FROM items i
           JOIN itemData id ON id.itemID = i.itemID
           JOIN fields f ON f.fieldID = id.fieldID
           JOIN itemDataValues idv ON idv.valueID = id.valueID
           WHERE f.fieldName = 'title' AND idv.value LIKE ? ESCAPE '\\' LIMIT 5""",
        (f"%{pat}%",),
    )
    return [r[0] for r in cur.fetchall()]


def _read_pdf_magic(path: str) -> Optional[bool]:
    """True iff the file starts with %PDF and is reasonably sized."""
    try:
        if os.path.getsize(path) < 1000:
            return False
        with open(path, "rb") as f:
            return f.read(4) == b"%PDF"
    except OSError:
        return None


def has_real_pdf(con, parent_key: Optional[str], title: Optional[str],
                 storage_dir: str) -> dict:
    """Verify a single parent item has a real PDF on disk.

    Tries the parent key first; if no PDF attachment is found (or no key), falls
    back to a title LIKE match. Returns a result dict with has_real_pdf + details.
    """
    candidates: list[tuple[str, tuple]] = []
    if parent_key:
        att = _attachment_for_parent(con, parent_key)
        if att:
            candidates.append((parent_key, att))
    if not candidates and title:
        for pk in _parent_keys_by_title(con, title):
            att = _attachment_for_parent(con, pk)
            if att:
                candidates.append((pk, att))
    if not candidates:
        return {"has_real_pdf": False, "reason": "no_pdf_attachment_in_db"}
    for pk, (att_key, filename) in candidates:
        fp = os.path.join(storage_dir, att_key, filename or "")
        if _read_pdf_magic(fp):
            try:
                size = os.path.getsize(fp)
            except OSError:
                size = None
            return {"has_real_pdf": True, "parent_key": pk,
                    "attachment_key": att_key, "filename": filename, "size": size}
    return {"has_real_pdf": False, "reason": "attachment_file_not_pdf_or_missing",
            "attachment_key": candidates[0][1][0]}


def verify_items(items: list, storage_dir: str, sqlite_path: str) -> list:
    """Attach a `real_pdf` field to each item (key-first, title fallback)."""
    try:
        con = _connect_ro(sqlite_path)
    except sqlite3.Error as e:
        return [{"real_pdf": {"has_real_pdf": False, "verify_skipped": True,
                              "reason": f"db_open: {e}"}} for _ in items]
    try:
        out = []
        for it in items:
            r = has_real_pdf(con, it.get("key"), it.get("title"), storage_dir)
            it2 = dict(it)
            it2["real_pdf"] = r
            out.append(it2)
        return out
    finally:
        con.close()


def scan_all(storage_dir: str, sqlite_path: str,
             limit: int = VERIFY_ALL_HARD_MAX) -> dict:
    """Whole-library ground-truth scan (Problem 3 'ground truth')."""
    try:
        con = _connect_ro(sqlite_path)
    except sqlite3.Error as e:
        return {"verify_skipped": True, "reason": f"db_open: {e}"}
    try:
        cur = con.execute(
            """SELECT a.key, ia.path, p.key FROM items p
               JOIN itemAttachments ia ON ia.parentItemID = p.itemID
               JOIN items a ON a.itemID = ia.itemID
               WHERE ia.contentType = 'application/pdf' LIMIT ?""",
            (limit * 2,),
        )
        real, missing = [], []
        for att_key, path, parent_key in cur.fetchall():
            filename = _strip_storage_prefix(path)
            fp = os.path.join(storage_dir, att_key, filename or "")
            if _read_pdf_magic(fp):
                real.append({"attachment_key": att_key, "parent_key": parent_key,
                             "filename": filename})
            else:
                missing.append({"attachment_key": att_key, "parent_key": parent_key})
        return {"total_pdf_attachments": len(real) + len(missing),
                "real_pdf_count": len(real), "missing_or_bad_count": len(missing),
                "real": real, "missing": missing}
    finally:
        con.close()

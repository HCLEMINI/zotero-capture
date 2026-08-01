#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""zotero-capture skill — find duplicate / junk entries (read-only by default).

After repeated captures the library accumulates duplicates (same paper, multiple
entries) and CF-verification-page junk ('请稍候…' saved as webpage). This script
FINDS them. Deletion is opt-in via --apply and requires Zotero to be closed
(writing zotero.sqlite while Zotero runs corrupts the DB).

  python cleanup.py                   # dry-run: list dups + junk
  python cleanup.py --apply           # soft-delete (-> Zotero trash). Needs Zotero closed.

Soft-delete only marks rows in `deletedItems`; empty the trash in the Zotero GUI
to physically remove them. A .bak copy of zotero.sqlite is taken before any write.
"""
import argparse
import os
import shutil
import socket
import sys
import io
import sqlite3
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

CF_JUNK = ("请稍候", "just a moment", "attention required", "checking your browser",
           "enable javascript", "访问验证", "安全检查", "one more step")


def _zotero_running(port=23119):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def find_problems(con):
    """Return (duplicates, junk). duplicates: list of (title, [keys]). junk: list of (key, type, title)."""
    # duplicates: same DOI or same title appearing >1 among journal articles
    dups = []
    for field in ("DOI", "title"):
        rows = con.execute(
            f"""SELECT v.value, GROUP_CONCAT(i.key) FROM items i
                JOIN itemData d ON d.itemID=i.itemID
                JOIN fields f ON f.fieldID=d.fieldID
                JOIN itemDataValues v ON v.valueID=d.valueID
                JOIN itemTypes it ON it.itemTypeID=i.itemTypeID
                WHERE f.fieldName=? AND it.typeName='journalArticle' AND v.value<>''
                GROUP BY v.value HAVING COUNT(*)>1""",
            (field,),
        ).fetchall()
        for val, keys in rows:
            dups.append((field, val[:70], keys.split(",")))

    # junk: non-journal items whose title matches CF verification phrases
    junk = []
    rows = con.execute(
        """SELECT i.key, it.typeName,
             (SELECT v.value FROM itemData d JOIN itemDataValues v ON d.valueID=v.valueID
                JOIN fields f ON f.fieldID=d.fieldID
                WHERE d.itemID=i.itemID AND f.fieldName='title')
           FROM items i JOIN itemTypes it ON it.itemTypeID=i.itemTypeID
           WHERE it.typeName IN ('webpage','attachment')"""
    ).fetchall()
    for key, typ, title in rows:
        t = (title or "").lower()
        if any(k in t for k in CF_JUNK):
            junk.append((key, typ, title))
    return dups, junk


def main():
    p = argparse.ArgumentParser(
        description="Find (and optionally soft-delete) duplicate/junk Zotero entries.")
    p.add_argument("--apply", action="store_true",
                   help="soft-delete found duplicates (keep earliest) + junk. Needs Zotero CLOSED.")
    p.add_argument("--data-dir",
                   default=os.environ.get("ZCB_ZOTERO_DATA_DIR"))
    args = p.parse_args()
    db = os.path.join(args.data_dir, "zotero.sqlite")
    if not os.path.isfile(db):
        print(f"ERROR: {db} not found (set ZCB_ZOTERO_DATA_DIR)", flush=True)
        sys.exit(2)

    con = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
    dups, junk = find_problems(con)
    con.close()

    print(f"duplicate groups: {len(dups)}", flush=True)
    to_delete = []
    for field, val, keys in dups:
        print(f"  [{field}] {val}  -> keys: {', '.join(keys)} (keep first)", flush=True)
        to_delete.extend(keys[1:])  # keep earliest
    print(f"CF junk entries: {len(junk)}", flush=True)
    for key, typ, title in junk:
        print(f"  {key} {typ:10s} {(title or '')[:60]}", flush=True)
        to_delete.append(key)

    if not args.apply:
        print(f"\nDRY-RUN: {len(to_delete)} entries would be soft-deleted. "
              "Re-run with --apply to proceed (Zotero must be closed).", flush=True)
        return

    if _zotero_running():
        print("ABORT: Zotero is running (port 23119 open). Close it before writing zotero.sqlite.", flush=True)
        sys.exit(1)
    bak = db + f".bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    shutil.copy(db, bak)
    print(f"backup -> {bak}", flush=True)
    wcon = sqlite3.connect(db)
    n = 0
    for key in to_delete:
        row = wcon.execute("SELECT itemID FROM items WHERE key=?", (key,)).fetchone()
        if not row:
            continue
        wcon.execute("INSERT OR IGNORE INTO deletedItems (itemID) VALUES (?)", (row[0],))
        n += 1
    wcon.commit()
    wcon.close()
    print(f"soft-deleted {n} entries. Open Zotero and empty the trash to physically remove.", flush=True)


if __name__ == "__main__":
    main()

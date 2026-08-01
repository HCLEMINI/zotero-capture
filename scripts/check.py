#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""zotero-capture skill — confirm what was ACTUALLY ingested (sqlite ground truth).

The connector's `success` field is NOT reliable: CF verification pages
('请稍候…' / 'Just a moment…') get saved as webpages with success:true. This
reads zotero.sqlite read-only and lists items added in the last N minutes with
type + title, so you can tell real journalArticle entries from junk.

  python check.py            # last 30 min
  python check.py --minutes 60
"""
import argparse
import os
import sys
import io
import sqlite3

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)


def main():
    p = argparse.ArgumentParser(
        description="List recently-ingested Zotero items (sqlite ground truth).")
    p.add_argument("--minutes", type=int, default=30)
    p.add_argument("--data-dir",
                   default=os.environ.get("ZCB_ZOTERO_DATA_DIR"))
    args = p.parse_args()
    db = os.path.join(args.data_dir, "zotero.sqlite")
    if not os.path.isfile(db):
        print(f"ERROR: {db} not found (set ZCB_ZOTERO_DATA_DIR)", flush=True)
        sys.exit(2)
    con = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
    rows = con.execute(
        """
        SELECT i.key, i.dateAdded, it.typeName,
          (SELECT v.value FROM itemData d JOIN itemDataValues v ON d.valueID=v.valueID
             JOIN fields f ON d.fieldID=f.fieldID
             WHERE d.itemID=i.itemID AND f.fieldName='title') AS title
        FROM items i JOIN itemTypes it ON i.itemTypeID=it.itemTypeID
        WHERE i.dateAdded > datetime('now', ?)
        ORDER BY i.dateAdded DESC
        """,
        (f"-{args.minutes} minutes",),
    ).fetchall()
    con.close()
    real = sum(1 for _, _, t, _ in rows if t == "journalArticle")
    print(f"items added in last {args.minutes} min: {len(rows)} "
          f"({real} journalArticle, {len(rows) - real} other/junk)", flush=True)
    for key, when, typ, title in rows:
        flag = "" if typ == "journalArticle" else "  <- NON-JOURNAL (possible CF verification-page junk)"
        print(f"  {key} {typ:14s} {(title or '')[:70]}{flag}", flush=True)


if __name__ == "__main__":
    main()

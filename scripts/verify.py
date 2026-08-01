#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""zotero-capture skill — verify real PDFs by Zotero storage %PDF header.

The connector's `pdf_status:"ok"` is NOT reliable. This reads zotero.sqlite
read-only and checks the storage file header for the %PDF magic — the only
ground truth (Problem 3 of the CNKI postmortem).

  python verify.py --keys ABCD1234,EFGH5678
  python verify.py --all
"""
import argparse
import json
import os
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "engine"))

from zotero_claude_bridge.config import Config
from zotero_claude_bridge.capture import run_verify_pdfs


def main():
    p = argparse.ArgumentParser(
        description="Verify real PDFs by Zotero storage %PDF header (ground truth).")
    p.add_argument("--keys", help="comma-separated parent item keys")
    p.add_argument("--all", action="store_true", help="scan the whole library")
    args = p.parse_args()
    a = {}
    if args.all:
        a["all"] = True
    elif args.keys:
        a["keys"] = [k.strip() for k in args.keys.split(",") if k.strip()]
    else:
        print("ERROR: use --keys k1,k2 or --all", flush=True)
        sys.exit(2)
    res = run_verify_pdfs(Config(), a)
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

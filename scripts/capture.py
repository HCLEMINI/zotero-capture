#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""zotero-capture skill — capture paper URLs into local Zotero.

Drives the patched Zotero Connector via WSBridge (NO MCP layer). Launch it,
refresh any tab in Edge to wake the MV3 service worker, and the connector
connects within ~20s.

  python capture.py --urls urls.txt --verify-pdfs --no-stop-on-dropoff --out res.json
  python capture.py --url https://www.sciencedirect.com/science/article/...
"""
import argparse
import asyncio
import json
import os
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "engine"))

from zotero_claude_bridge.config import Config, setup_logging
from zotero_claude_bridge.ws_bridge import WSBridge
from zotero_claude_bridge.capture import run_capture_urls


def load_urls(args):
    if args.url:
        return [args.url]
    if args.urls:
        v = args.urls
        if os.path.isfile(v):
            with open(v, encoding="utf-8") as f:
                return [ln.strip() for ln in f
                        if ln.strip() and not ln.strip().startswith("#")]
        return [u.strip() for u in v.replace("\n", ",").split(",") if u.strip()]
    return []


async def main_async(args):
    urls = load_urls(args)
    if not urls:
        print("ERROR: no URLs. Use --url <one> or --urls <file|comma-list>", flush=True)
        return 2
    cfg = Config()
    log = setup_logging(cfg.log_level)
    cap_args = {
        "batch_size": args.batch_size, "batch_delay": args.batch_delay,
        "concurrency": args.concurrency, "verify_pdfs": args.verify_pdfs,
        "verify_delay": args.verify_delay, "stop_on_dropoff": args.stop_on_dropoff,
        "dropoff_threshold": args.dropoff_threshold, "verbose": args.verbose,
    }
    print(f"[capture] {len(urls)} URLs | batch={args.batch_size} conc={args.concurrency} "
          f"verify={args.verify_pdfs} stop_on_dropoff={args.stop_on_dropoff}", flush=True)
    bridge = WSBridge(cfg.host, cfg.port, log)
    task = asyncio.create_task(bridge.start())
    try:
        for _ in range(40):
            if bridge.bind_state != "starting":
                break
            await asyncio.sleep(0.25)
        if bridge.bind_state != "owned":
            print(f"[capture] ABORT: bind_state={bridge.bind_state} — another session owns "
                  f"port {cfg.port}. Close other Claude sessions / zcb scripts and retry.",
                  flush=True)
            return 1
        print(f"[capture] WS bound on :{cfg.port}. Waiting for connector "
              "(refresh any tab in Edge to wake the service worker)...", flush=True)
        connected = False
        for i in range(180):
            if bridge.is_connected():
                connected = True
                break
            if i % 4 == 0:
                print(f"  waiting connector... {i * 0.5:.0f}s", flush=True)
            await asyncio.sleep(0.5)
        if not connected:
            print("[capture] ABORT: connector did not connect. "
                  "Refresh any tab in Edge, ensure the patched connector is loaded.", flush=True)
            return 1
        print("[capture] connector connected, capturing...", flush=True)
        res = await run_capture_urls(bridge, cfg, cap_args, urls)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
            print(f"[capture] full result -> {args.out}", flush=True)
        print("[capture] SUMMARY:", json.dumps(res.get("summary", {}), ensure_ascii=False), flush=True)
        for i, r in enumerate(res.get("results", []), 1):
            ok = r.get("success")
            err = r.get("errorType") or r.get("error") or ""
            items = r.get("items") or []
            title = (items[0].get("title") if items else "")[:60]
            key = (items[0].get("key") if items else "")
            rp = ""
            if items and isinstance(items[0].get("real_pdf"), dict):
                rp = "PDF:Y" if items[0]["real_pdf"].get("has_real_pdf") else "PDF:N"
            print(f"  [{i:02d}] {'OK ' if ok else 'FAIL'} {rp:>6} {key:9s} {err} | {title}", flush=True)
        s = res.get("summary", {})
        return 0 if s.get("fail", 0) == 0 else 1
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


def main():
    p = argparse.ArgumentParser(
        description="Capture paper URLs into local Zotero via the patched connector (no MCP).")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--url", help="single URL to capture")
    g.add_argument("--urls", help="comma-separated URLs OR path to a file (one URL per line, '#' comments)")
    p.add_argument("--batch-size", type=int, default=18)
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--batch-delay", type=int, default=10, help="seconds to sleep between batches")
    p.add_argument("--verify-pdfs", action="store_true",
                   help="check Zotero storage PDF header after each batch (ground truth)")
    p.add_argument("--verify-delay", type=int, default=8, help="seconds to wait before verifying a batch")
    p.add_argument("--stop-on-dropoff", dest="stop_on_dropoff", action="store_true", default=True,
                   help="(default) stop when a batch hits the throttling cliff")
    p.add_argument("--no-stop-on-dropoff", dest="stop_on_dropoff", action="store_false",
                   help="keep going past dropoff (use for English publishers: missing PDF from no subscription is normal)")
    p.add_argument("--dropoff-threshold", type=float, default=0.5)
    p.add_argument("--verbose", action="store_true", help="keep abstracts/attachments in output")
    p.add_argument("--out", help="write full result JSON to this path")
    args = p.parse_args()
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()

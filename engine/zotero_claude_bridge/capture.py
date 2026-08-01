"""Capture logic for the zotero-capture skill (NO MCP layer).

These functions drive the patched Zotero Connector over the WSBridge: batched
capture with throttling protection, storage-%PDF ground-truth verification, and
return abridging. They are the reusable engine extracted from the former MCP
server; scripts call them directly — no stdio, no registration, no fixed-port
process lifecycle.

Problem coverage (see CF/CNKI postmortems in the skill's references):
  - Problem 1/2: WSBridge.bind_state -> port_in_use, never a misleading
    extension_disconnected when another session owns the port.
  - Problem 3/4/6: pdf_verify storage %PDF ground truth (pdf_status is unreliable).
  - Problem 4: server-side batching + dropoff (throttling-cliff) detection.
  - Problem 7: abridged items by default (drop abstractNote/tags/attachments).
"""
import asyncio
import logging
import os
import time

from . import pdf_verify
from .config import Config
from .schemas import (
    Action, DEFAULT_BATCH_SIZE, DEFAULT_BATCH_DELAY, DEFAULT_BATCH_CONCURRENCY,
    DEFAULT_DROPOFF_THRESHOLD, DEFAULT_VERIFY_DELAY,
)

_log = logging.getLogger("zotero_claude_bridge")


def paths(cfg: Config):
    """Return (storage_dir, sqlite_path) under the configured Zotero data dir."""
    storage = os.path.join(cfg.zotero_data_dir, "storage")
    sqlite_path = os.path.join(cfg.zotero_data_dir, "zotero.sqlite")
    if not os.path.isdir(storage):
        return None, None
    return storage, sqlite_path


_ITEM_KEEP = ("title", "itemType", "key", "DOI", "url", "date",
              "publicationTitle", "pdf_status", "real_pdf", "ISBN", "need_verification")


def abridge_item(it):
    """Strip heavy fields (abstractNote/tags/attachments) from a captured item."""
    if not isinstance(it, dict):
        return it
    out = {k: it[k] for k in _ITEM_KEEP if k in it and it[k] is not None}
    cr = it.get("creators")
    if isinstance(cr, list) and cr:
        out["creators_short"] = [
            (c.get("lastName") or c.get("name") or c.get("firstName") or "")
            for c in cr if isinstance(c, dict)
        ][:6]
    return out


def abridge_result(r):
    """Abridge a CaptureResult. Failures are returned unchanged (keep error/hint)."""
    if not isinstance(r, dict) or r.get("success") is False:
        return r
    r2 = dict(r)
    if "items" in r2:
        r2["items"] = [abridge_item(it) for it in r2["items"]]
    r2["abridged"] = True
    return r2


def run_verify_pdfs(cfg: Config, args: dict) -> dict:
    """Ground-truth PDF check via Zotero storage %PDF header (Problem 3)."""
    storage, sqlite_path = paths(cfg)
    if not storage:
        return {"success": False, "verify_skipped": True,
                "error": (f"Zotero storage not found under {cfg.zotero_data_dir}; "
                          "set ZCB_ZOTERO_DATA_DIR.")}
    if args.get("all"):
        return {"success": True, **pdf_verify.scan_all(storage, sqlite_path)}
    keys = args.get("keys") or []
    if not keys:
        return {"success": False, "error": "provide 'keys' (parent key list) or set all=true"}
    items = [{"key": k} for k in keys]
    verified = pdf_verify.verify_items(items, storage, sqlite_path)
    real = sum(1 for v in verified
               if isinstance(v.get("real_pdf"), dict) and v["real_pdf"].get("has_real_pdf"))
    return {"success": True, "requested": len(keys), "real_pdf_count": real, "verified": verified}


async def run_capture_urls(bridge, cfg: Config, args: dict, urls: list) -> dict:
    """Server-side batched capture with throttling protection (Problem 4) and
    optional storage-%PDF verification (Problem 3)."""
    start = time.time()
    batch_size = int(args.get("batch_size") or DEFAULT_BATCH_SIZE)
    batch_delay = int(args.get("batch_delay") or DEFAULT_BATCH_DELAY)
    concurrency = max(1, min(int(args.get("concurrency") or DEFAULT_BATCH_CONCURRENCY), 5))
    stop_on_dropoff = bool(args.get("stop_on_dropoff", True))
    threshold = float(args.get("dropoff_threshold") or DEFAULT_DROPOFF_THRESHOLD)
    verify = bool(args.get("verify_pdfs", False))
    verify_delay = int(args.get("verify_delay") or DEFAULT_VERIFY_DELAY)
    verbose = bool(args.get("verbose", False))

    storage, sqlite_path = (None, None)
    if verify:
        storage, sqlite_path = paths(cfg)
        if not storage:
            _log.warning("verify_pdfs requested but storage not found; disabling verify")
            verify = False

    batches = [urls[i:i + batch_size] for i in range(0, len(urls), batch_size)]
    all_results: list = []
    succ = fail = real_pdf_count = completed = 0
    stopped = False
    stop_reason = None

    for bi, batch in enumerate(batches):
        resp = await bridge.request(
            Action.CAPTURE_URLS,
            {"urls": batch, "concurrency": concurrency, "stopOnError": False},
            timeout=max(120, 90 * len(batch)),
        )
        if not isinstance(resp, dict):
            resp = {"success": False, "errorType": "unknown",
                    "error": "non-dict response", "results": []}
        batch_results = resp.get("results", []) if resp.get("success") else []

        batch_succ = batch_real = 0
        need_ver = False
        if verify and batch_results:
            await asyncio.sleep(verify_delay)  # let async PDF downloads land on disk

        for r in batch_results:
            items = r.get("items") or []
            if verify:
                items = pdf_verify.verify_items(items, storage, sqlite_path)
                r["items"] = items
                if any(isinstance(it, dict) and it.get("real_pdf", {}).get("has_real_pdf")
                       for it in items):
                    batch_real += 1
            if r.get("success"):
                batch_succ += 1
            else:
                fail += 1
            if r.get("need_verification"):
                need_ver = True
            all_results.append(r)

        succ += batch_succ
        real_pdf_count += batch_real
        completed += 1
        _log.info("capture batch %d/%d: success=%d real_pdf=%d need_ver=%s",
                  bi + 1, len(batches), batch_succ, batch_real, need_ver)

        # throttling-cliff detection (Problem 4)
        if stop_on_dropoff and batch_results:
            if verify:
                ratio = (batch_real / batch_succ) if batch_succ > 0 else 0.0
            else:
                ratio = batch_succ / len(batch_results)
            if ratio < threshold or need_ver:
                stopped = True
                stop_reason = "verification_required" if (need_ver and ratio >= threshold) else "dropoff"
                _log.warning("stopping at dropoff after batch %d: ratio=%.2f need_ver=%s",
                             bi + 1, ratio, need_ver)
                break

        if bi < len(batches) - 1 and not stopped:
            await asyncio.sleep(batch_delay)

    if not verbose:
        all_results = [abridge_result(r) for r in all_results]

    summary = {
        "total": len(urls), "success": succ, "fail": fail,
        "batches": len(batches), "completed_batches": completed,
        "stopped_at_dropoff": stopped, "stop_reason": stop_reason,
        "durationMs": int((time.time() - start) * 1000),
    }
    if verify:
        summary["real_pdf_count"] = real_pdf_count
    return {"success": True, "results": all_results, "summary": summary}

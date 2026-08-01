# zotero-capture

A [Claude Code](https://claude.com/claude-code) **skill** that captures web papers (CNKI / ScienceDirect / ACS / Nature / Springer / MDPI / Wiley / arXiv / ...) into the **local Zotero** client — metadata + full-text PDF — by driving a patched [Zotero Connector](https://github.com/zotero/zotero-connectors) extension. Pure Python scripts, **no MCP server**.

## How it works

```
Claude Code (calls scripts/*.py)
   │  python imports WSBridge
   ▼
WSBridge (WS server, ws://127.0.0.1:24731/bridge)
   │  WebSocket
   ▼
Patched Zotero Connector (browser extension, claude-bridge.js)
   │  reuses the translator + itemSaver pipeline
   ▼
Local Zotero 7 (127.0.0.1:23119)
```

The engine is the **connector extension** (hundreds of translators). This skill replaces the earlier MCP stdio layer with direct script calls — fewer moving parts (no MCP registration, no port conflicts across sessions, no `/mcp` reconnect).

## Requirements

1. **Patched connector** — the fork [`HCLEMINI/zotero-connectors-claude-code`](https://github.com/HCLEMINI/zotero-connectors-claude-code) built and loaded in Chrome/Edge (`build/manifestv3/`). It includes the Cloudflare/interstitial fix (wait for full load + retry empty capture).
2. **Zotero 7 desktop** running (`curl http://127.0.0.1:23119/connector/ping` → 200).
3. `PYTHONUTF8=1` for the scripts (avoids GBK crashes on Chinese output).
4. **One Claude session** at a time on port 24731 (the script self-detects `port_in_use`).

## Install

```bash
git clone https://github.com/HCLEMINI/zotero-capture.git ~/.claude/skills/zotero-capture
export ZCB_ZOTERO_DATA_DIR="/path/to/your/Zotero"   # the dir containing zotero.sqlite + storage/
```

Then in Claude Code say **"zotero-capture"** or **"capture these papers into Zotero"**.

## Scripts

| script | purpose |
|---|---|
| `scripts/capture.py` | batch / single capture → local Zotero. Flags: `--urls` / `--url`, `--batch-size`, `--concurrency`, `--batch-delay`, `--verify-pdfs`, `--stop-on-dropoff` / `--no-stop-on-dropoff`, `--verbose`, `--out` |
| `scripts/verify.py` | ground-truth PDF check by Zotero storage `%PDF` header (`--keys` / `--all`) |
| `scripts/check.py` | confirm what was ACTUALLY ingested (sqlite; the connector's `success` field is unreliable) |
| `scripts/cleanup.py` | find / soft-delete duplicates + CF-junk entries (dry-run / `--apply`) |

```bash
PYTHONUTF8=1 python ~/.claude/skills/zotero-capture/scripts/capture.py --urls urls.txt --verify-pdfs --out res.json
PYTHONUTF8=1 python ~/.claude/skills/zotero-capture/scripts/check.py --minutes 30
```

## Lessons baked in (from real batch ingests)

- **`success` / `pdf_status` are NOT reliable** — Cloudflare interstitials get saved as `请稍候…` webpages with `success:true`. Always confirm with `check.py` (sqlite) + `verify.py` (storage `%PDF` header).
- **Cloudflare is "wrong completion timing", not "too fast"** — the patched connector waits for the page to fully stop loading (complete + non-interstitial title + stable) and retries empty captures. Raising the timeout alone does nothing.
- **English publishers miss PDFs from no subscription** — that is not a failure; use `--no-stop-on-dropoff` so metadata still ingests.
- **CNKI**: use the landing link `kns.cnki.net/kcms2/article/abstract?v=...`, not the `ss.zhizhen.com/goread?...` redirect (the translator does not recognize the redirect).

See `SKILL.md` for the full workflow and the complete CNKI/Cloudflare postmortem lessons.

## Configuration

Environment variables:
- `ZCB_ZOTERO_DATA_DIR` — your Zotero data directory (contains `zotero.sqlite` + `storage/`). **Required** for `verify.py` / `check.py` / `cleanup.py`.
- `ZCB_HOST` (default `127.0.0.1`), `ZCB_PORT` (default `24731`) — the WS bridge endpoint (must match the connector's `claude-bridge.js`).
- `ZCB_LOG_LEVEL` (default `INFO`).

## Related

- Patched connector (the engine): [`HCLEMINI/zotero-connectors-claude-code`](https://github.com/HCLEMINI/zotero-connectors-claude-code) — fork of `zotero/zotero-connectors`.

## License

AGPL-3.0-or-later (consistent with upstream Zotero Connectors).

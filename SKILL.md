---
name: zotero-capture
description: 把网页论文（知网/ScienceDirect/ACS/Nature/Springer/MDPI/Wiley/arXiv 等学术页）批量抓取入库本地 Zotero（元数据+全文PDF），复用改造的 Zotero Connector 扩展的 translator 引擎；纯脚本直驱（无 MCP），自带限流分批+PDF真实校验+sqlite核实+去重清垃圾。【触发条件严格】仅当用户明确说出 skill 名 "zotero-capture" 或斜杠命令 /zotero-capture，或明确说"抓论文到 Zotero/批量入库 Zotero/把网页论文存进 Zotero/抓取文献入库"时才使用。【不要】因用户说"找文献/调研文献/搜论文/下载论文（未说入库 Zotero）/读这篇论文/整理文献库/归类文献"等而自动触发——这些先反问"是否要用 zotero-capture skill 把论文抓取入库 Zotero？"确认后再进入。知网检索用 lib-search，谷歌学术用 scholar-search，库内归类用 zotero-classify-refs，均非本 skill。
---

# zotero-capture：网页论文 → 本地 Zotero

把学术网页论文批量抓取入库本地 Zotero（元数据 + 全文 PDF）。引擎是**改造的 Zotero Connector 扩展**（几百个 translator），本 skill 用 Python 脚本直驱，**不走 MCP**（避免注册 / 重连 / 24731 端口冲突）。

## 架构（脚本直驱，无 MCP）

```
Claude（调 scripts/*.py）
   │  python import WSBridge
   ▼
WSBridge（WS server，ws://127.0.0.1:24731/bridge）  ← 脚本自带 bind_state 自检
   │  WebSocket
   ▼
改造的 Zotero Connector（Edge 扩展，claude-bridge.js 连本 WS）
   │  复用 translator + itemSaver
   ▼
本地 Zotero 7 客户端（127.0.0.1:23119）
```

**不可替代**：connector 扩展 + Zotero 客户端。**已去掉**：MCP stdio 层（原 zotero-claude-bridge 仓库，引擎代码已迁入本 skill 的 engine/）。

## 依赖（必须先满足）

1. **改造的 connector 扩展**装在 Edge，加载自 `<connector-build>/build/manifestv3`（含修复 A/B/C：CF 验证页 blocklist 等待 + 抓空重试 + 前台 tab）。
2. **Zotero 7 桌面**在跑（`curl http://127.0.0.1:23119/connector/ping` 返回 200）。
3. 全程 `PYTHONUTF8=1`（中文输出防 GBK 崩）。
4. **单会话**：只开一个 Claude / zcb 脚本用 24731（多开抢端口，bind_state 会报 port_in_use）。

## 核心流程（4 步）

### 1. 唤醒 connector（首次 / 断连后）
脚本起 WSBridge 后，connector 不主动连——**到 Edge 刷新任意一个网页标签**唤醒 MV3 service worker，~20s 内自动连上。脚本打印 `waiting connector...` 直到 `connector connected`。

### 2. 抓取 capture
```bash
PYTHONUTF8=1 python scripts/capture.py --urls urls.txt --verify-pdfs --out res.json
PYTHONUTF8=1 python scripts/capture.py --url https://www.sciencedirect.com/... --verify-pdfs
```
关键参数：
- `--urls`：逗号分隔 URL，或文件路径（每行一个，`#` 注释）
- `--batch-size 18 --batch-delay 10 --concurrency 4`：分批 + 批间 sleep（限流保护）
- `--verify-pdfs`：每批抓完按 storage `%PDF` 校验真实 PDF
- `--stop-on-dropoff`（默认）/ `--no-stop-on-dropoff`：**知网用默认**（限流拐点该停）；**英文出版商用 `--no-stop-on-dropoff`**（无订阅缺 PDF 正常，不该中断元数据入库）
- `--verbose`：保留摘要/附件（默认精简省 token）

URL 规则：
- **知网用落地链接** `https://kns.cnki.net/kcms2/article/abstract?v=...`，**不要** `ss.zhizhen.com/goread?...` 中转链接（translator 不识别）
- ScienceDirect / ACS / Nature / Springer / MDPI / Wiley 直链 OK（CF 站 connector 修复后会等过验证页）

### 3. 核实 check（**必做！success 不可信**）
```bash
PYTHONUTF8=1 python scripts/check.py --minutes 30
```
查 zotero.sqlite 最近入库，区分真 `journalArticle` vs CF 垃圾（webpage 标题「请稍候…」）。**bridge 的 `success:true` 会假报**（CF 验证页被抓空或当 webpage 入库），唯一地面真相是这条 sqlite 查询。

### 4. 校验 + 清理
```bash
PYTHONUTF8=1 python scripts/verify.py --keys KEY1,KEY2     # 单/多 key 真实 PDF
PYTHONUTF8=1 python scripts/verify.py --all                 # 全库扫
PYTHONUTF8=1 python scripts/cleanup.py                      # 找重复 + CF 垃圾（dry-run）
PYTHONUTF8=1 python scripts/cleanup.py --apply              # 软删（需关 Zotero + 自动备份）
```

## ★ 关键教训（焊死，别重蹈覆辙）

1. **`success` / `pdf_status` 都不可信**——CF 验证页「请稍候…」会被当 webpage 误存、抓空也报 success。**唯一核实**：`check.py`（sqlite 查 dateAdded + title）+ `verify.py`（storage `%PDF`）。
2. **CF 站不是"抓太快"，是 connector 等错完成时机**——原始 `_waitForTabStatus` 在验证页第一次 complete 就抓。修复版（blocklist + stableMs 稳定等待 + 抓空重试）已在 connector 扩展。**调大 timeout 没用**，要靠重试等到 CF 通过跳转到真实页。
3. **单条 `capture_url` 能透传 options（loadTimeoutMs/stableMs），批量 `_captureUrls` 不透传**——CF 慢站（ACS / Nature）优先用单条 `capture.py --url`；纯批量对 CF 站可能不够。
4. **英文出版商缺 PDF 多因无订阅**，不是失败——`--no-stop-on-dropoff` 让元数据照样入库。
5. **DOI / BibTeX 备用路线**（CF 死活过不去 / 只想要元数据）：`curl -H "Accept: application/x-bibtex" https://doi.org/<doi>`、`api.crossref.org/works?query.bibliographic=<title>`、`api.openalex.org/works?search=<title>`（国内可达不走 CF）。坑：Elsevier 新文 / MDPI 会议刊在 CrossRef 命中相似旧文，须 `returned_title vs 原文 sim≥0.85` 核验。最后一步拖 `.bib` 进 Zotero 必须手动（Zotero 无本地 import API）。
6. **重复条目**：重抓同篇会建重复，`cleanup.py` 找出（留最早）。垃圾「请稍候」webpage + Snapshot 也用 cleanup 清。
7. **多会话抢 24731**：bind_state=port_in_use 时脚本 ABORT 并提示——关掉其它 Claude / 脚本再重试，**别去折腾扩展**。
8. **connector 改动只在 ASCII 副本 `your connector build clone` 改 + build**；中文开发仓 `your connector source repo (zotero-connectors-claude-code fork)` 是 git remote（中文+方括号路径让 build.sh 的 rsync/perl/jq 挂）。

## 故障排查

| 现象 | 处理 |
|---|---|
| `bind_state=port_in_use` | 其它会话占了 24731。关其它 Claude / zcb 脚本，重试 |
| `connector did not connect` | Edge 刷新任意网页唤醒 SW；确认 connector 扩展已加载 |
| 大面积 `no_translator` / 抓空 | CF 站：改单条 `--url` + 调 connector loadTimeoutMs；或走 DOI/BibTeX 路线 |
| `zotero_offline` | 启动 Zotero 桌面 |
| 抓了一堆但 check.py 显示网页垃圾 | CF 验证页误存，`cleanup.py --apply` 清；真文重抓 |

## 文件位置

- `engine/zotero_claude_bridge/`：WSBridge + pdf_verify + capture 逻辑（从原 MCP server 抽出，无 stdio 层）
- `scripts/`：capture.py / verify.py / check.py / cleanup.py
- connector 扩展（引擎）：`your connector build clone`（build 后装 Edge）
- connector 源仓库：`your connector source repo (zotero-connectors-claude-code fork)`
- Zotero 数据：`your Zotero data dir (set ZCB_ZOTERO_DATA_DIR)`（zotero.sqlite + storage/）
- 历史踩坑复盘：本地笔记（未公开；核心教训已固化在本 SKILL.md 的 ★ 关键教训）

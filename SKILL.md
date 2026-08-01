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

1. **改造的 connector 扩展**装在 Edge，加载自 `<connector-build>/build/manifestv3\`（含修复 A/B/C：CF 验证页 blocklist 等待 + 抓空重试 + 前台 tab）。
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

### 2.5 获取知网落地链接（源是超星 zhizhen / 表格题录 / 第三方时）

外部来源（Excel 题录表、超星发现 `ss.zhizhen.com/detail_...`、维普/万方导出、EndNote 题录）给的"链接"常**不是**知网落地链接，translator 不识别 → 必须先换成 `kns.cnki.net/kcms2/article/abstract?v=...`。**办法：kimi-webbridge 驱动 Edge 在知网按篇名检索，逐篇取结果链接。** 落地链接在结果列表 `a.fz14` 元素的 `href` 里。

Python 直驱 daemon（`127.0.0.1:10086`；Windows 下用 Python 发 JSON 避免 shell 破坏中文）骨架：

```python
def call(action, args, session="cnki-cap"):
    body = json.dumps({"action": action, "args": args, "session": session}).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:10086/command", data=body,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read().decode("utf-8"))
def ev(code):
    v = call("evaluate", {"code": code})["data"]["value"]
    return json.loads(v) if isinstance(v, str) else v
def norm(s):
    return re.sub(r"[^一-龥A-Za-z0-9]", "", re.sub(r"\s+", "", s or "")).lower()

# navigate 预填搜索框并做一次主题检索
call("navigate", {"url": f"https://kns.cnki.net/kns8/defaultresult/index?kw={quote(title)}"})
# 读结果、按标题 norm 严格相等匹配拿 href
lst = ev("(()=>{return JSON.stringify(Array.from(document.querySelectorAll('a.fz14')).slice(0,30).map(a=>({text:a.innerText.replace(/\\s+/g,' ').trim(),href:a.href})))})()")
hit = next((it for it in lst if norm(it["text"]) == norm(title)), None)
```

**四个坑（全是实战踩过，别重蹈）**：

1. **默认 `kw=` 是"主题"检索**，按相关性排序，目标论文常排到前 12 之外 → 切到**篇名**字段再搜（篇名精确，几乎必中且排第一）。
2. **切字段不能只 `el.click()`**：下拉在 `.sort.reopt`，选中值写进隐藏 `#selectfield`（属性 `value`/`korder`/`data-opt`）。CNKI 的 jQuery handler 要 **`mouseenter`+`click` 双事件**才触发，光 `click` 字段不更新。篇名 = `li[data-val='TI']`。验证：`#selectfield.value=='TI'` 且 `.sort.reopt .sort-default` 文本=="篇名"。
3. **标题含连字符（如"应力-渗流"）篇名精确检索会漏**：用**去连字符/去标点变体**重搜（如"岩体水力压裂应力渗流耦合近场动力学模拟"），知网标题本身保留连字符，norm 相等即可锁定。
4. **标题匹配用 norm 严格相等**（去空白+标点+小写），别用包含匹配——主题检索的近似结果会误中。norm 相等 = 同一篇。

切篇名一次成（开下拉 + 选 TI + 返回新字段状态）的可复用 JS：

```js
(()=>{return JSON.stringify((function(){
  var w=document.querySelector('.sort.reopt'), def=w.querySelector('.sort-default'), list=w.querySelector('.sort-list');
  if(list) list.style.display='block'; def.click();
  var ti=w.querySelector(".sort-list li[data-val='TI']");
  ti.dispatchEvent(new MouseEvent('mouseenter',{bubbles:true})); ti.click();
  var sf=document.getElementById('selectfield');
  return {val:sf.value, korder:sf.getAttribute('korder'), label:def.querySelector('span').innerText.trim()};
})())})()
```

> 主动在知网检索用 **lib-search** skill；本节仅解决"已有题录表、只需把第三方链接换成知网落地链接喂给 capture"的场景。

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
9. **源链接不是知网时**（超星 `ss.zhizhen.com/detail_...` / Excel 题录表 / 维普万方导出），translator 不识别 → 先按上文「2.5 获取知网落地链接」用 kimi-webbridge 在知网篇名检索换成 `kns.cnki.net` 落地链接。四坑：默认主题检索要切篇名 / 切字段需 `mouseenter`+`click` 双事件 / 含连字符标题用去标点变体 / norm 严格相等匹配（禁包含匹配）。

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

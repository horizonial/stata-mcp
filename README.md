# stata-mcp

**A Windows-first, production-oriented Stata execution server for LLM / agent applications (Model Context Protocol).**
**面向 LLM/agent 应用、Windows 优先的 Stata 执行工具服务器（MCP）。**

[GitHub](https://github.com/horizonial/stata-mcp) · MIT License · Python 3.12 · Stata 17+ (pystata)

It embeds pystata (Stata 17+) in-process and exposes Stata as machine-readable MCP tools: run code, load data, inspect data, read structured results, export graphs, background long jobs with interruption, data preview and command help.
它进程内嵌 pystata，把 Stata 暴露成一组机器可读的 MCP 工具：执行代码、载入数据、查看数据、读结构化结果、导出图、后台长任务与中断、数据预览、命令帮助。

Built after a code-level review of the 4 mainstream open-source Stata MCP projects (tmonk/mcp-stata, SepineTam/mcp-for-stata, haoyu-haoyu/stata-ai-fusion, hanlulong/stata-mcp), to close their shared gaps. Passed 7 rounds of adversarial audit (167 tests).
本仓库是在代码级调研 4 个主流开源 Stata MCP 之后自研的，用于补它们共同的缺口；历经 7 轮对抗式审计（167 个测试全绿）。

---

## 1. Features / 特性

| EN | CN |
|---|---|
| **Universal structured results** — any e-class estimation command (`regress`/`logit`/`xtreg`/`mixed`/…) auto-yields regression JSON (`coefs`, se/t/p/ci, N, r2, full e()-scalars). No command-name enumeration. | **通用结构化结果**——任意估计命令自动产出回归 JSON（系数/se/t/p/ci、N、r2、全量 e() 标量），无需枚举命令名。 |
| **Session isolation + self-healing** — each session is a worker subprocess; lazy start, idle reclamation, crash auto-rebuild, Windows Job Object kills orphans on parent death (no license leak). | **会话隔离 + 自愈**——每会话一个 worker 子进程；懒启动、空闲回收、崩溃自动重建；父进程死亡时 Windows Job Object 自动清理孤儿进程（防 license 泄漏）。 |
| **Provenance / reproducibility** — every result carries `command_hash`, `data_signature`, `exec_seq` and a runnable `do_file`. | **来源追溯 / 可复现**——每个结果带 command_hash、数据指纹、执行序号和可复现的 do_file。 |
| **Command journal + replay** — session keeps a command log; on crash/reset it returns the history so an agent can rebuild state. | **命令日志 + 重放**——会话保留命令日志；崩溃/重置时随结果返回历史，供 agent 重建状态。 |
| **Rich error object** — unified `error` (command_failed / timeout / crashed / start_failed), rc, error_class, session_reset, replay. | **统一错误对象**——error（command_failed/timeout/crashed/start_failed）+ rc + error_class + session_reset + replay。 |
| **Background tasks** — submit a long command, poll status, interrupt; per-session routing; bounded table (TTL/caps). | **后台任务**——提交长命令、轮询状态、可中断；按会话路由；任务表有界（TTL/上限）。 |
| **Multi-session routing** — real `session_id` end-to-end (schema → resolver → background), strict validation (no silent fallback). | **多会话路由**——session_id 端到端贯通（schema→解析→后台），严格校验（非法不再静默回退）。 |
| **Security** — variable-name whitelist, path auditor (fail-closed), restricted mode, DNS-aware SSRF guard on URLs. | **安全**——变量名白名单、路径审计（fail-closed）、受限模式、URL 的 DNS-aware SSRF 守卫。 |
| **Graphs back to agent** — exported images returned as base64 `ImageContent` (multimodal agent sees them). | **图直接给 agent**——导出图片以 base64 ImageContent 返回（多模态 agent 能直接看到）。 |
| **Chinese-Windows tuned** — UTF-8 primary with GBK fallback, forward-slash Stata paths, Chinese filenames tested. | **中文 Windows 适配**——UTF-8 主路 + GBK 回退、Stata 正斜杠路径、中文文件名实测。 |
| **Pluggable** — Backend / Tool / ResultParser registries; swap driver = one assembly point. | **可插拔**——Backend / Tool / ResultParser 注册表；换驱动只改一处装配。 |

## 2. Strengths vs. open-source alternatives / 相对开源的优点

- **Structured results, not log text.** All 4 OSS projects return cleaned log text; this one returns regression JSON straight from Stata memory (`sfi` + Mata), generic over every estimation command.
  **结构化结果而非 log 文本**：4 个开源项目都只回清洗文本；本项目用 sfi+Mata 直读内存产出回归 JSON，对任意估计命令通用。
- **Survives Windows.** Orphan-process and license-leak handling, crash fail-fast, GBK-aware — where others ship Unix-only assumptions or half-baked Windows.
  **Windows 上真正能跑**：孤儿进程/license 泄漏处理、崩溃 fail-fast、GBK 适配——其它项目多带 Unix 假设或 Windows 半成品。
- **Reliability as a feature.** Worker ready-handshake, per-command timeout + alive-poll (no 300s hangs), replay on reset.
  **可靠性当功能做**：worker 启动握手、超时 + 存活轮询（不再 300s 干等）、重置带重放。
- **Thin by design.** ~3.7k lines vs mcp-stata's 8.6k+; no UI-HTTP channel, no Rust, no research-orchestration bloat.
  **刻意做薄**：约 3.7k 行 vs mcp-stata 8.6k+；不带 UI-HTTP 通道、Rust、研究编排膨胀。
- **Honest about limits.** It documents what it is NOT (a sandbox; see §4/§7).
  **对边界诚实**：明确写出它"不是"什么（沙箱；见 §4/§7）。

## 3. Honest limitations / 诚实的劣势

| EN | CN |
|---|---|
| **restricted mode is NOT a sandbox** — it is a best-effort "command whitelist + path audit" injection interceptor. Stata's syntax surface (frame prefix, `command()`, macros, `#delimit`) cannot be fully statically parsed. For truly untrusted code the correct answer is: do NOT expose free-code execution at all (use only whitelisted structured tools). | **受限模式不是沙箱**——它是尽力而为的"命令白名单 + 路径审计"注入拦截器。Stata 语法面（frame 前缀、command()、宏、#delimit）无法被完整静态解析。对真正不可信的内容，正确做法是根本不提供自由代码执行（只用白名单结构化工具）。 |
| **URL guard cannot stop DNS-rebinding end-to-end** — static + DNS-resolution filtering is a pre-filter; full defense needs connection-layer re-validation (trusted download/proxy). | **URL 守卫无法端到端防 DNS rebinding**——静态 + DNS 解析过滤只是前置；完整防御需连接层二次校验（可信下载/代理）。 |
| **stdio only (single client)** — no HTTP transport yet (official mcp 2.x supports it; not wired in). | **只有 stdio（单客户端）**——暂无 HTTP 传输（官方 mcp 2.x 支持，未接入）。 |
| **One machine / one process model** — sessions are local workers; no remote serving, no multi-user auth. | **单机/单进程模型**——会话是本机 worker；无远程服务、无多用户鉴权。 |
| **Requires licensed local Stata 17+** and per-session license seats; real-engine tests need Stata present (CI mock-only). | **需本地正版 Stata 17+** 且每会话占一个 license 席位；真引擎测试需本机有 Stata（CI 仅跑 mock）。 |
| **pystata/embedded coupling** — depends on Stata's pystata; Windows-only tested, macOS/Linux untested. | **与 pystata/内嵌耦合**——依赖 Stata 的 pystata；仅 Windows 实测，macOS/Linux 未测。 |
| **Output capture is in-process stdout swap** — fine for MCP (fd-based), but background tasks can't interleave other threads' prints. | **输出捕获是进程内 stdout 交换**——对 MCP 无碍（基于 fd），但后台任务期间其他线程 print 会被占用。 |

## 4. Security posture / 安全定位

- L1 variable-name whitelist; L3 centralized path auditor (fail-closed); URL guard (https, no IP-literal/userinfo/localhost, DNS-resolve rejects private/loopback, optional host whitelist).
- **restricted mode** (`stata_run(restricted=true)` or `security.restricted_mode=true`): command whitelist for in-memory analysis on already-loaded data; blocks shell/erase, external `do/run/include`, network imports (`webuse`, `import fred/haver`), nested-command carriers (`frame:`/`table, command()`), macro/compound-quote paths, and out-of-directory file access. **Best-effort, not a sandbox.** Load data through `stata_load_data`, not free code.
- Audit log records each tool call (code hashed, not stored raw); privacy-hashed diagnostics.

**中文**：L1 变量名白名单；L3 集中路径审计（fail-closed）；URL 守卫（强制 https、拒 IP/userinfo/localhost、DNS 解析拒私网/回环、可选域名白名单）。**restricted 模式** = 对"已载入内存的数据"做分析的命令白名单；拦 shell/erase、外部 do/run/include、网络 import、嵌套命令载体（frame:/table, command()）、宏/复合引号路径、目录外读写。**尽力而为，非沙箱**。载数据请走 `stata_load_data`，别用自由代码。

## 5. Requirements / 环境

- Windows (tested on Windows 11) · Stata 17+ with pystata (`utilities/pystata`) · Python 3.12+ · dep: only official `mcp`
- 中文 Windows 同样支持（编码与中文文件名已测）。

## 6. Install / 安装

```bash
pip install -e .
python -m stata_mcp.server
```
Stata 根目录默认 `C:\Program Files\Stata18`，可用环境变量 `STATA_HOME` 覆盖。Lazy start：列工具不占 license，首次执行才拉起引擎。

## 7. Claude Code

```bash
claude mcp add stata-mcp -- python -m stata_mcp.server
```
或 `.mcp.json` 的 stdio 配置（见仓库 `config` 示例）。Most tools accept `session_id` to target a session.

## 8. Tools / 工具（10）

| Tool | 说明 |
|---|---|
| `stata_run(code, background?, restricted?, session_id?)` | 执行代码；自动附结构化回归 JSON + provenance；后台执行返回 job_id |
| `stata_load_data(source, clear?, session_id?)` | 载入 dta/csv/xlsx（路径审计，限授权目录）|
| `stata_inspect_data(action, variables?, session_id?)` | describe/summarize/codebook（变量名白名单）|
| `stata_get_results(session_id?)` | 读 e()/r() 结构化 + 数据形状 |
| `stata_data_rows(rows?, session_id?)` | 读数据集前 N 行（结构化二维数组）|
| `stata_export_graph(format, name?, filename?, session_id?)` | 导出图，base64 ImageContent 直接回给 agent |
| `stata_get_help(topic, session_id?)` | 查 Stata 官方帮助（.sthlp）|
| `stata_session_history(last?, session_id?)` | 会话命令日志（重放原料）|
| `stata_break(session_id?)` | 打断指定会话当前命令 |
| `stata_task_status(job_id)` | 后台任务状态/结果（全局 job 查询）|

## 9. Structured result example / 结构化结果示例

```json
{"cmd":"regress","depvar":"mpg","N":74.0,"r2":0.6515,
 "coefs":[{"var":"weight","coef":-0.0060,"se":0.0005,"t":-11.6,"p":1e-18,"ci":[...]}],
 "scalars":{...},
 "provenance":{"command_hash":"...","data_signature":"74:12(71728):...","exec_seq":2,
               "do_file":"sysuse auto, clear\nregress mpg weight"}}
```
通用机制：判 `e(b)` 存在即可提取，任意估计命令自动覆盖；`e()` 全量标量通用抓取；provenance 供 agent"数值接地"。

## 10. Architecture / 架构

```
server.py        MCP stdio；make_context() 装配；审计日志
session.py       Session（懒启动 worker 代理）+ SessionManager（上限/回收/自愈/Job Object）+ 命令日志
stata/worker.py  worker 子进程（引擎+执行+结构化+break 线程+孤儿看门狗+ready 握手）
results/         通用系数提取 + 会话快照 + 数据预览（sfi 直读 + Mata 统计量）
guard/           L1 白名单 + L3 路径审计 + restricted（命令白名单注入拦截器）
platform/job.py  Windows Job Object（父死子亡）
tools/           10 个工具 @register；tasks.py 后台任务表（有界）
```
抽象可插拔：ExecutionBackend / Tool / ResultParser。详见 `ARCHITECTURE.md`、`DESIGN.md`（含全部踩坑与每轮审计记录）。

## 11. Testing / 测试

```bash
python -m unittest discover -s tests -t .   # 167 用例（mock + 真引擎回归；无 Stata 也跑 mock）
python -m unittest tests.test_real_stata -v # 真引擎正确性：比对我们结构化提取 vs Stata 官方数值
```
7 轮对抗式审计 + 每轮回归锁死，过程记在 `DESIGN.md`（§27a–27h）。

## 12. Known boundaries / 已知边界（诚实声明）

- **restricted 非沙箱**（见 §3/§4）；**DNS rebinding 需连接层兜底**。
- 会话状态在内存；跨进程重启的持久化归上层（agent 台账）。
- macOS/Linux 未实测；无 CI（真引擎需 Stata license）。

## License

MIT —— 自研；仅借鉴开源项目的**思路**（代码级调研，未复制其实现）。

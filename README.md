# stata-mcp

面向 **LLM / agent 应用**的 Stata 执行工具服务器（Model Context Protocol）。进程内嵌 pystata（Stata 17+），把 Stata 暴露成一组机器可读的工具：执行代码、载入数据、查看数据、读结果、导出图、后台长任务与中断、数据预览、帮助查询。

**核心差异化**（相对 GitHub 上 4 个主流 Stata MCP）：**通用结构化结果**（任意估计命令自动产出回归 JSON，不枚举命令名）、**会话隔离 + 自愈**（进程级 worker + Job Object 防泄漏）、**中文 Windows 一线适配**、**可插拔抽象**、**来源追溯**（每个结果带 command_hash / data_signature / 可复现 do-file）。

## 环境要求

- Windows / macOS / Linux（主要开发与实测于 **Windows 11 + Stata 18 MP**）
- **Stata 17+**（自带 pystata；MP/SE/IC 均可）
- Python **3.12+**（依赖仅官方 `mcp`）

## 安装

```bash
pip install -e .
python -m stata_mcp.server
```

Stata 根目录默认 `C:\Program Files\Stata18`，可用 `STATA_HOME` 覆盖。

## 接入 Claude Code

```bash
claude mcp add stata-mcp -- python -m stata_mcp.server
```

或 `.mcp.json` stdio 配置。服务器惰性启动：列工具不占 license，首次执行才拉起引擎。

## 工具一览（10 个）

| 工具 | 说明 |
|---|---|
| `stata_run(code, background?, restricted?)` | 执行代码；**自动附结构化回归结果**（coefs/N/r2/scalars + provenance）；`background=True` 后台执行 |
| `stata_load_data(source, clear?)` | 载入 .dta/.csv/.xlsx；**路径审计**（限工作目录 + 授权目录），支持 https URL |
| `stata_inspect_data(action, variables?)` | describe/summarize/codebook；**变量名白名单**防注入 |
| `stata_get_results()` | 只读查询 e()/r() 结构化 + 数据集形状 |
| `stata_data_rows(rows?)` | 读当前数据集前 N 行（结构化），agent 直接看数据 |
| `stata_export_graph(format, name?, filename?)` | 导出图，**直接返回图片给多模态 agent** |
| `stata_get_help(topic)` | 查命令帮助（findfile .sthlp，纯文本） |
| `stata_session_history(last?)` | 会话命令日志（崩溃后重放原料） |
| `stata_break()` | 打断当前命令 |
| `stata_task_status(job_id)` | 查后台任务 |

## 结构化结果示例

```json
{
  "cmd": "regress", "depvar": "mpg", "N": 74.0, "r2": 0.6515,
  "coefs": [{"var": "weight", "coef": -0.0060, "se": 0.0005, "t": -11.6, "p": 1e-18, "ci": [...]}],
  "scalars": {"ll": ..., "df_r": ..., ...},
  "provenance": {"command_hash": "...", "data_signature": "74:12(71728):...", "exec_seq": 2,
                 "do_file": "sysuse auto, clear\nregress mpg weight"}
}
```

**通用机制**：系数提取判断 `e(b)` 存在即可，任意 e-class 估计命令（regress/logit/xtreg/anova/mixed/sureg…）自动覆盖，无需登记命令名；`e()` 全量标量通用抓取。**来源追溯**：provenance 含命令哈希、数据指纹、可复现 do-file，供 agent"数值接地"。

## 架构

```
server.py（薄）  MCP stdio；make_context() 唯一装配点；审计日志
session.py       Session（懒启动 worker 代理）+ SessionManager（上限/空闲回收/自愈/Job Object 防泄漏）+ 命令日志
stata/worker.py  worker 子进程（引擎 + 执行 + 结构化 + 独立 break 线程 + 孤儿看门狗）
stata/pystata_backend.py   stdout 交换捕获 + 全局 scalar rc + StataSO_SetBreak 中断
results/         ResultParser + 通用系数提取 + 会话快照（sfi 直读 + Mata 算统计量）
guard/           L1 变量名白名单 + L3 数据路径审计 + 受限模式（防 shell/erase/越权路径）
output/          SMCL 清理 / 编码 / 错误分类 / 截断 / 隐私哈希
platform/job.py  Windows Job Object（父死子亡，防 license 泄漏）
tools/           10 个工具，@register 注册
```

三个抽象均可插拔：`ExecutionBackend`、`Tool`、`ResultParser`。详见 `ARCHITECTURE.md` 与 `DESIGN.md`。

## 测试

```bash
python -m unittest discover -s tests -t .      # 92 用例（mock，无 Stata 也能跑）
python -m unittest tests.test_real_stata -v     # 4 用例（需本机 Stata，正确性回归台）
```

真引擎正确性回归台把结构化提取和 Stata 官方数值比对，防"悄悄给错数字"。引擎级实测记录见 `spikes/` 与 `DESIGN.md`。

## 已知限制

- `background` 执行期间 `sys.stdout` 被捕获（pystata 捕获是进程级）；MCP 通信实测不受影响。
- 会话状态在内存（worker 崩溃自动 reset + 附重放日志）；跨服务器重启的持久化归上层台账。

## License

MIT（自研；仅借鉴开源项目的思路，未复制其代码）。

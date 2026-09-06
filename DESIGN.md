# stata-mcp 架构设计（v0.1，开发基准）

> 目标：面向 **LLM / agent 应用开发**的一个自研 Stata MCP 服务器。**不追求从零重写**：代码级吸收 GitHub 上 4 个主流开源 Stata MCP（mcp-stata / mcp-for-stata / stata-ai-fusion / stata-mcp）的优点，补上它们共同的缺口，然后一步步开发 + 测试。
>
> 调研基于 GitHub 上 4 个主流开源 Stata MCP 的仓库级源码阅读（mcp-stata / mcp-for-stata / stata-ai-fusion / stata-mcp）。设计过程的更多文档见 `docs/`。

## 1. 需求（钉在桌上）

- 平台：Windows 11（中文，GBK）；Stata 18 MP + pystata；Python 3.12。
- 服务于**自建的多步 agent**：需要持久会话 + 结构化结果 + 可靠中断。
- 同时可被任何 MCP 客户端（Claude Code 等）接入，独立可演示。
- 人力少、要能长期演进：薄服务器、少依赖、不赌第三方库内部实现。

## 2. 借鉴 / 补缺 映射

| 现有项目 | 我们吸收的优点 | 我们补它的缺（不学的地方） |
|---|---|---|
| tmonk/mcp-stata | 进程级会话隔离；out-of-band break；持久 SMCL 日志 + 偏移流式；图导出绕行；clear 后重载启动 do | 不做 8600 行单体；不把研究编排塞进 server；不碰它的事件循环 / RC 读取 bug |
| SepineTam/mcp-for-stata | guard 静态展开（保留作受限模式）；集中路径审计；隐私日志 | 不做无状态批处理为主线；Windows 执行层半成品要自己重做 |
| haoyu-haoyu/stata-ai-fusion | PipeSession 哨兵+日志追踪思想；SessionManager 并发模型 | 不用 pexpect（Windows 无 PTY） |
| hanlulong/stata-mcp | 命名日志与用户日志共存；log 字节偏移流式；多 worker 隔离 | 不 monkey-patch fastapi-mcp 内部；不做单会话无锁全局 |

**共同缺口（= 我们的差异化）**：① 结构化结果（都只给文本）；② 中文 Windows 编码（都硬编码 UTF-8）；③ Windows 进程管理（都 Unix 假设）。

## 3. 技术决策

| # | 决策 | 理由 / 代价 |
|---|---|---|
| D1 | **pystata 内嵌**为默认驱动，抽象 `Backend` 接口 | 保状态 + 能读写 Stata 内存；可切批处理。代价：license 席位、init 慢 |
| D2 | 会话：MVP **单进程**起步 → 二期按需拆 **worker 子进程** | 隔离/崩溃可控是 mcp-stata 优点，但不提前付复杂度。代价：单会话先无隔离 |
| D3 | 输出捕获 = **进程内 `sys.stdout` 交换**（按调用、串行锁保护），不再依赖文件日志 | 实测：pystata 输出走 Python sys.stdout，进程内可完整捕获。绕开文件独占锁、`log close _all`、临时文件。代价：单引擎内需串行（本就如此）；换 worker 进程时各进程各自交换 |
| D4 | **编码探测链** UTF-8→GBK→latin-1 统一读写；日志实测为 **UTF-8** | 中文环境第一优先级，四家都没做。GBK 仅作老文件回退 |
| D5 | 中断：`sfi.breakIn()` 走**独立线程**；硬杀走平台层 | 中断不占命令队列；Windows 用 taskkill / Job Object |
| D6 | 传输：**stdio 起步**（官方 mcp SDK），工具层与传输解耦 | 简单、够单客户端；需要时再加 HTTP，不引入重框架 hack |
| D7 | 结构化结果：工具返回 `{text, structured, rc, error_class, graphs}` 信封 | 为 agent 推理；是我们的差异。代价：每类命令写 schema 解析 |
| D8 | 工具 / 结果 schema 注册化；薄服务器 | 加能力 = 加模块 + 注册；避免膨胀 |

## 4. 目录结构（规划）

```
stata-mcp/
├── pyproject.toml
├── src/stata_mcp/
│   ├── server.py            # MCP 组装 + stdio 入口（薄）
│   ├── tools/               # 工具（注册化）
│   │   ├── __init__.py      # registry
│   │   └── run.py           # v1: run code / do-file
│   ├── stata/
│   │   ├── backend.py       # Backend 接口
│   │   ├── pystata_backend.py
│   │   └── discovery.py     # 找 Stata/pystata + preflight
│   ├── output/
│   │   ├── capture.py       # 命名日志包裹 + 读回 + 偏移
│   │   ├── encoding.py      # 探测链
│   │   ├── smcl.py          # SMCL 清理
│   │   └── errors.py        # rc / 错误分类
│   ├── results/             # （二期）结构化 schema
│   ├── platform/process.py  # Windows 进程层
│   └── config.py
├── spikes/                  # 一次性验证脚本（点火/输出/编码）
└── tests/
```

## 5. 路线（开发顺序 = 测试顺序）

1. **P0 点火**：pystata `init` + `display` 跑通（现在）。
2. **P1 执行核**：do 文件 + 命名日志包裹 → 返回 `{text, rc}`；验证编码。
3. **P2 MCP 壳**：1 个 `stata_run` 工具，stdio 端到端。
4. **P3 结构化**：reg/esttab → JSON schema。
5. **P4 健壮**：会话/worker 隔离、中断、图、更多工具。
6. **P5 agent 接入**（另项目）。

每个 P 以 spike 实测数据为准，先验证后定稿。

## 6. Spike 实测结论（2026-09-02）

| Spike | 结论 |
|---|---|
| 01 点火 | `config.init(edition="mp")` 2.8s 拉起 Stata 18；`sfi.Scalar` 双向读写内存 OK。⚠️ `c(flavor)=IC`、license 为网络版 2026-09-09 到期 |
| 02 执行核 | Stata 18 **text log 实测为 UTF-8**（gbk 解码失败）；用户 `log close _all` 会关掉我们的命名日志（tail 丢失）；错误 rc 用**全局 scalar** 稳定拿到（rc=111） |
| 03 捕获机制 | **pystata 输出走 Python sys.stdout，进程内交换即可完整捕获**（marker=True）→ 定案 D3 不依赖文件日志 |

待续：回显命令行去噪、回归表（边框字符）渲染、中文经 stdout 捕获往返是否无损、超长输出分块。

## 7. P2 交付与实测（2026-09-02）

P2 由 haiku 子 agent 实现，pro review + 实测通过。代码在 `src/stata_mcp/`（envelope / stata/{backend,pystata_backend} / output/{encoding,smcl} / tools/{__init__,run} / config / server），依赖仅官方 `mcp>=2,<3`。

实测结论（spikes/04、05）：
- 真实引擎下 rc 语义正确：成功=0、出错=111、多行块中间出错=111、capture 尾部=0；出错后引擎存活可继续执行；
- MCP stdio 端到端跑通：initialize→tools/list→tools/call 全链路 OK，`rc` 正确映射为 `is_error`；
- 持久会话验证：先存 `scalar _x` 后一次调用读回 42；
- 已知待改进：多行命令回显行（`capture noisily {`/`. sysuse ...`）未去净，`strip_smcl` 对多行块回显的处理留待 P3/P5 完善；mcp 2.x API 字段为 snake_case（`server_info`/`is_error`），测试脚本需对应。

## 8. P3 结构化结果路线（spike 定案）

**核心结论：结构化结果走 `sfi.Matrix` 直读内存 + Mata 算统计量，彻底绕开文本解析。**

实测（spikes/06-10）：
- `sfi.Matrix.get("e(b)")` 返回系数向量（list of list）；`Matrix.getColNames("e(b)")` 拿变量名（含 `_cons`）；`Matrix.get("e(V)")` 返回协方差矩阵，对角线开方即标准误；
- **系数表**用一段 Mata 统一算 coef/se/t/p/ci，t 分布（regress，用 `e(df_r)`）与 z 分布（logit，`missing(df)` 判空）都验证数值正确：
  ```mata
  mata:
      b = st_matrix("e(b)")'
      se = sqrt(diagonal(st_matrix("e(V)")))
      t = b :/ se
      df = .
      if (st_global("e(df_r)") != "") df = strtoreal(st_global("e(df_r)"))
      if (missing(df)) { p = 2*normal(-abs(t)); crit = invnormal(0.975) }
      else { p = 2*ttail(df, abs(t)); crit = invttail(df, 0.025) }
      st_matrix("_coefs", (b, se, t, p, b-crit*se, b+crit*se))
  end
  ```
- 模型统计标量直读：`e(N)`/`e(r2)`/`e(r2_a)`/`e(F)`/`e(df_m)`/`e(df_r)`/`e(rank)`；宏 `e(cmd)`/`e(depvar)`/`e(vcetype)`；
- r 类命令（summarize/tabulate/ttest）用 `Scalar.getValue("r(...)")` 直读（如 r(N)/r(mean)/r(t)/r(p)/r(r)/r(c)）。

**Mata 踩坑记录**：`e(b)` 是 1×k 行向量、`diagonal(V)` 是 k×1 列向量，混用会 conformability error(3200)；必须统一列向量（`b = st_matrix("e(b)")'`）。Mata 里 `x == .` 当 x 为 missing 时返回 missing（不当 true），必须用 `missing(df)` 判断。

P3 实现要点（给 haiku 的约束）：ResultParser 协议（§6.5）按 `e(cmd)` 路由；估计类命令产出 `{cmd, depvar, N, r2/chi2, coefs[]}`，r 类命令产出 `{cmd, scalars{}}`；stata_run 工具在 rc=0 且存在 e(cmd) 时填充 Envelope.structured。

**P3 落地后追加的实测修正（比 spike 阶段多踩 3 个坑）**：
- **e() 残留导致结构化结果过期**（真 bug）：跑完 regress 再跑 `display`/`summarize`，e(cmd) 仍是旧值，会误附上一次回归的 structured。修法：`ExecutionResult` 加 `e_changed` 字段，backend 用执行前后 e() 指纹（e(cmd)/e(depvar)/e(N)/e(df_r) 四元组）比对，`run.py` 仅在 `e_changed=True` 时才 try_parse。
- **t/z 分布判断不能在 Mata 里做**：`st_numscalar("e(df_r)")` 对缺失返回 missing，但 `missing(df)` 在 if 里行为不可靠（实测 logit 误走 ttail 报 conformability 3200）。改为 **Python 侧读 `e(df_r)`**，None → z 分布（normal/invnormal），有值 → t 分布（把 df 注入 Mata），逻辑可预测可单测。
- **logistic 与 logit 的 e(b) 相同**（都是 log-odds，非 OR），无需特殊处理。
- Mata `if (c) {a; b}` 单行花括号会 illegal arglist(r3000)，必须多行展开。

## 9. P4 guard 安全层（2026-09-02）

P4 由 haiku 实现，pro review + 单测通过（39 tests OK，import 不触发引擎）。交付 4 块纯逻辑（不碰 Stata）：
- `guard/validate.py`：L1 白名单正则（变量名/标识符/文件名，挡设备名/通配符/控制符/路径分隔）；
- `guard/data_path.py`：L3 DataPathAuditor（本地路径 commonpath 判包含 + realpath 防符号链接逃逸；URL 默认 https + 拒 IP 字面量/userinfo + 域名白名单；全程 fail-closed）；
- `config.py` 升级：分层配置（env > project > user > default），**security 区 user > project**（吸收 mcp-for-stata，防项目配置放松用户安全边界），用标准库 tomllib；
- `output/privacy.py`：路径/URL 确定性哈希脱敏（保留盘符+文件名，中间目录/query/userinfo 哈希或丢弃）。

设计取舍（P4 决定）：
- **砍掉 L2 命令静态展开黑名单**：那是"跑陌生人 do 文件"场景，agent 跑可信代码会被误拦；留到"受限模式"再加。
- URL 守卫**默认开启**（修 mcp-for-stata 默认关闭的 SSRF 隐患）。
- 待续：URL 守卫的 localhost/内网域名边界（当前只拒 IP 字面量）留待有明确内网需求再定。

P5 起，这些会被 `stata_load_data`（L3 路径审计）与 `stata_inspect_data`（L1 变量名校验）等新工具消费。

## 10. P5a 数据工具（2026-09-02）

P5a 由 haiku 实现，pro review + 真实 dta 实测通过（70 单测 + 引擎实测）。新增 3 工具：
- `stata_load_data(source, clear)`：L3 审计（默认允许 cwd + config 授权目录），按扩展名路由 use/import；结构化 `{source, N, k}`。实测：英文/中文 dta 均正确（N=7156/k=40、N=2820/k=37），越界文件正确拒绝。
- `stata_inspect_data(action, variables)`：L1 变量名白名单校验（`mpg; drop _all` 注入正确拒绝）；describe 结构化变量名清单、summarize 单变量 `{N,mean,sd,min,max}`。实测正确。
- `stata_get_results()`：只读，e() 优先（try_parse 回归结构化），否则解析 return list 的 r() 标量/宏。

实测修正（比 haiku 交付多踩 1 个坑）：
- **`sfi.Data` 没有 `getVarNames()`**，是 `getVarCount()` + `getVarName(i)` 循环（haiku 方法名猜错，实测才暴露，已修）。

设计决定：
- `get_results` 语义 = "e() 优先"：返回**最后估计结果**（agent 关心的研究状态），非"最后命令结果"。因为 e() 是持久的估计状态、r() 是易变命令结果。已写入 docstring。
- `inspect_data` 多变量 summarize 的逐变量结构化暂不做（Stata 的 r() 只保留最后变量统计，要逐变量得循环 execute），留待需要。

待续（P5b/P5c）：中断 sfi.breakIn、图导出。

## 11. P5b 中断（2026-09-02）

pro 亲自 spike 定案 + 实现（引擎/线程关键，不派 haiku）。核心结论：
- **pystata 没有 `sfi.breakIn`**（那是 mcp-stata 作者不同版本的写法）。真正的中断 API 是 **`pystata.config.stlib.StataSO_SetBreak`**（ctypes 函数，无参无返回，线程安全）。
- 中断机制：独立线程跑命令、主线程调 SetBreak 打断；打断后命令 rc=1、引擎存活、可继续；**串行多次打断安全**（hanlulong"SetBreak 只能调一次"警告在 Stata 18 不成立）。
- 无命令运行时误调 break 安全（不影响后续）。

交付：
- `PystataBackend.interrupt()`：封装 SetBreak，**不持锁**（否则与持锁阻塞的 execute 死锁），_ready 检查。
- `tasks.py` TaskRunner：后台任务表（submit/status），线程 + 锁保护。
- `stata_run(background=True)`：后台执行立即返回 job_id。
- `stata_break`：打断当前命令。`stata_task_status`：查状态/结果。

MCP e2e 实测：6 工具全注册，background 提交→status 返回 42（正确），break 正常。

已知限制（记录，非阻塞）：
- background 执行期间 `sys.stdout` 交换会劫持其他线程的 print（pystata 输出捕获是进程级）。MCP server 实测**不受影响**——mcp stdio_server 启动时用 os.dup 固定 private_fd、JSON-RPC 不经过 sys.stdout 对象；只影响 background 期间其他线程的 print（asyncio 主线程不 print）。若未来要彻底干净，background 任务可改走 log 文件捕获。

## 12. P5c 图导出（2026-09-02）

pro 亲自 spike + 实现。核心结论：
- **Stata 18 的 pystata 内嵌引擎能直接 `graph export` PNG/SVG**（无 mcp-stata 调研里的 rc=5100 崩溃），**不需要"graph save + 外部批处理"绕行**。
- **路径必须用正斜杠**：Stata 里 `\` 是转义符，Windows 绝对路径的反斜杠会误解析（`C:\Users` 的 `\U` 等）→ "file not found"。一律 `os.path` 归一后 `.replace("\\", "/")`。
- 图名检测：`graph dir` 列出内存图名。
- `graph export` 语法：`graph export "path", name(g1) replace`（name 与 replace 之间**不加逗号**）。

交付：`stata_export_graph(format, name?, filename?)` → 导出到 `cwd/_mcp_graphs/`，返回 `{path, format, size_bytes, graph}`，name/filename 均经 L1 白名单校验（防注入）。

实测：PNG（22490B, 720×432）、SVG（46920B）都真实导出可读，非法图名注入正确拒绝。

至此 7 个工具全部交付：stata_run / stata_load_data / stata_inspect_data / stata_get_results / stata_export_graph / stata_break / stata_task_status。下一阶段 P6 扩展性压力测试。

## 13. P6 扩展性压力测试（2026-09-02）

验证 ARCHITECTURE §5 的三个抽象承诺，全部用 stub backend（不碰 Stata）：
- **ExecutionBackend 可插拔** ✅：工具 handler 通过 `ctx.backend` 取后端，用 StubBackend 注入即可跑（test_extensibility 证明工具层不硬编码 pystata）。
- **Tool 可扩展** ✅：`@register` 装饰器动态注册，加工具 = 加模块 + 注册，不改 core。
- **ResultParser 可扩展** ✅：`@register` 动态注册，加命令解析器 = 加模块，不改 core。

顺带的架构改进：把 server.py 的 backend 装配点**显式化**为 `make_context()`（返回带 backend 的 SimpleNamespace）。换 backend（批处理 Stata / R / SAS）= 改这一处，工具层与 core 完全不感知。之前 `_call_tool` 传 None 走单例，是隐式装配。

测试命令（75 用例全绿）：
```
python -m unittest discover -s tests -t .
```

**项目状态**：MCP 服务器完成。7 工具 + 结构化结果 + guard 安全层 + 后台任务/中断 + 图导出 + 可插拔抽象，全部实测通过。

## 14. P7 多命令覆盖（2026-09-02，响应"其他命令也能提取系数吗"）

实测覆盖：**12 个估计命令全部正确提取系数**（用 auto + 构造面板）：

| 命令族 | 实测命令 | 结果 |
|---|---|---|
| 线性 | regress / areg / ivregress 2sls / xtreg fe·re·cluster | ✅ 系数 + t 分布 + r2/chi2 |
| 二值/离散 | logit / probit / oprobit / mlogit | ✅ 含 oprobit 的 cut 阈值、mlogit 的多方程前缀 |
| 计数 | poisson | ✅ |
| 受限 | tobit | ✅ 含 var(e.mpg) 方差参数 |

发现并修复两个问题：
- **mlogit 列名丢失方程前缀**（真 bug）：`Matrix.getColNames("e(b)")` 对多方程模型只返回纯变量名、丢了 outcome 号，导致 weight/price/_cons 重复、agent 无法区分系数属于哪个方程。修复：Mata 读 `st_matrixcolstripe("e(b)")`，组合规则 = **仅当 eq 是纯数字（outcome 号）才加 `eq:` 前缀**，否则用纯变量名（去掉 logit/probit 的 depvar 前缀、oprobit 的 "/" 切点标记）。
- **tobit 等命令漏注册**：补入 tobit/heckman/ologit/nbreg/glm/truncreg/gmm 等常用估计命令。

踩坑：MATA 加 for 循环花括号后，`str.format(df=...)` 把花括号当占位符报 KeyError，改用 `.replace("DF_PLACEHOLDER", ...)`。

**原理总结**：系数提取通用于**所有 e-class 估计命令**（都产生 e(b)/e(V)），r-class 命令（summarize/tabulate/ttest）不适用（走 get_results 的 return list 解析）。

## 15. P8 架构泛化：通用机制替代命令枚举（2026-09-02）

**问题**（回应"有无数的命令和系数，不能人为设置全"）：穷举命令名/系数类型不可扩展，是错误的。

**实测依据**（spike45）：所有 e-class 估计命令（regress/logit/xtreg/**anova/sureg**/…）的 `e(b)` 都是 **1×k 系数向量、e(V) 是 k×k 协方差矩阵**——这是 Stata 的统一约定。

**重构**：删除 `_COMMANDS` 命令名枚举，`try_parse` 改为三级路由：
1. `PARSERS` 注册表命中专用 parser → 用它（未来特殊结构的"按需加深"钩子）；
2. 否则 `e(b)` 可读 → **通用系数提取**（`parse_coef_table`），**不枚举命令名**；
3. 否则 → 最小兜底 `{cmd, N}`。

**实测验证**（spike46）：未注册命令全部自动覆盖——anova（8系数，含 1b.rep78 因子基准类）、sureg 联立方程、因子×连续交互、nbreg（含 lnalpha 过度离散参数）。

**结论**：命令数量不是问题——"系数表"结构对 e-class 是统一的，通用机制自动覆盖任意估计命令。真正需要"按需加深"的只有**非系数表结构**（方差分解、混合模型方差分量等），那是少数，且 PARSERS 注册表已预留钩子（注册即覆盖通用路径，不改核心）。

## 16. P8 补充：e() 全量标量抓取（回应"除了系数还有很多参数"）

**问题**：估计结果除了系数，还有辅助参数（sigma/rho/alpha/cut）和模型统计量（ll/aic/df/p/converged），每个命令不同。

**实测澄清**（spike47），两类参数规律不同：
- **辅助参数**（sigma/rho/alpha/cut）**已在 e(b) 里**（排在系数后面），通用系数提取已自动覆盖（tobit 的 var(e.mpg)、nbreg 的 lnalpha、oprobit 的 cut1-4）；
- **模型统计量**（ll/aic/bic/df_r/df_m/p/F/converged/alpha）散布在 e() 标量里，是统一的 `e(name)=value` 命名标量，**通用解析即可，不枚举命令**。

**实现**：`parse_coef_table` 在系数提取后，跑 `ereturn list`（纯显示、不改状态），用 `parse_ereturn_scalars` 通用解析 scalars 段，产出 `out["scalars"]`。

**实测**（tobit/nbreg/oprobit）：`ll`/`converged`/`df_r`/`df_m`/`p`/`chi2`/`alpha`/`r2_p` 全部自动抓取。

**最终结构化结果三层**：
1. `coefs`：系数 + 辅助参数（e(b) 通用提取）
2. `N`/`r2|r2_p|chi2`：关键拟合优度（直读 e() 标量）
3. `scalars`：e() 全量标量（ll/aic/converged/alpha...，通用解析）

全部通用机制，无命令枚举——"无数的参数"由 Stata 的 e-class 约定（e(b) 向量 + e() 标量）统一承载。

## 17. P9 修复：structured 协议层丢弃（2026-09-02）

**严重 bug**：`server.py` 的 `_call_tool` 只返回 `envelope.text`，把 `structured`/`error_class`/`graphs` 全丢弃——agent 通过 MCP 协议**拿不到结构化结果**（只能拿文本）。这是核心差异化在"最后一公里"丢失。（haiku 在 P3 交付时已标出，pro review 漏了。）

**修复**：`CallToolResult` 用 `structured_content=envelope.structured`，`error_class`/`graphs` 拼进 `_meta`。实测：MCP e2e 里 `structured_content` 非空，含 cmd/coefs/scalars。

## 18. 批判性审视（假设以研究者视角审查本项目）

详见正文。核心结论：**通用机制是对的，但有 3 类真缺陷 + 2 类取舍代价 + 2 类集成缺口**，最严重的是（已修）structured 丢弃、（未修）通用机制边界未验证 + 单进程无隔离。

## 19. P10 会话隔离 + 会话自愈（2026-09-03）

**动机**：批判性审视第 4 条（单进程无隔离）。吸收 mcp-stata（进程隔离）+ stata-mcp（自愈）的优点，但按更优方案自研，不照搬。

**关键架构洞察**（决定改动方向）：**结构化结果必须在 worker 内完成**——sfi 读的是进程内 Stata 内存（e(b)/e(V)/e()），主进程读不到 worker 的内存。因此"执行 + 结构化解析"整体移入 worker 子进程，主进程只做"安全（guard）+ 协议 + 组装"。

**相对两家的提升**（非照搬）：
- **懒启动**：worker 第一次 execute 才 spawn+init，不占 license 直到真正执行（vs mcp-stata 每 session 常驻）；
- **空闲回收**：会话空闲超时自动 close 释放 license（vs stata-mcp 预起 worker 池常驻）；
- **分层自愈**：execute 超时先 break 保状态 → 仍卡则 kill 重建；worker C 崩溃 → 下次 execute 自动重建，返回 `reset=True` 明确标记"数据已丢"（不静默）。

**架构**：
- `stata/worker.py`：worker 子进程（init pystata + execute + 结构化 + 独立 break 线程 + snapshot）
- `session.py`：Session（懒启动 + execute + interrupt + snapshot + is_idle + close）+ SessionManager（get_or_create + reap_idle）
- `results/parser.py` 加 `snapshot_state`（worker 内读 e()/r()/shape/variables）
- 工具层 `_resolve_backend` 返回 Session；结构化结果由 `session.execute`/`session.snapshot` 从 worker 拿回

**实测**：spike50（mp spawn + pystata 可行）、spike52（自愈：崩溃重建 reset 标记正确）、spike53（会话隔离下 regress/logit/持久状态/结构化全正常）、spike49（MCP 协议 structured_content 正常传回）。

**Windows spawn 坑**：入口脚本必须有 `if __name__ == "__main__"` 保护，否则子进程重新 import 时会在顶层递归 spawn（RuntimeError）。server.py 已符合。

**待续**：多 session 并发（session_id 路由已留）、license 上限、空闲回收的实测。

## 20. P11 补缺陷（2026-09-03）：通用机制边界 + 来源追溯

响应批判性审视第 2、7 条缺陷。

**缺陷 2（通用机制边界）**——实测发现两个真实问题并修复：
- **mixed 方差分量标识丢失**：`mixed mpg weight || rep78:` 的 e(b) 是 1×4，含"固定系数 + 2 个随机效应方差分量"，colstripe 里方差分量的 eq 是 `lns1_1_1`/`lnsig_e`（非纯数字），被旧规则"纯数字才加前缀"丢弃 → 3 个 `_cons` 无法区分。修复：colstripe 规则改为"**eq 为空 或 eq==depvar 名 → 纯变量名；否则 → eq:name**"。实测 5 命令列名全对：mixed 现在输出 `lns1_1_1:_cons`/`lnsig_e:_cons`。
- **形状检测防御**：`_has_eb` 加 `len(m)==1` 校验。若某命令 e(b) 是多行矩阵，通用转置/对齐假设失效会"悄悄给错数字"，现在 fail-closed 走 fallback 而非硬提取。

**缺陷 7（来源追溯）**——给结构化结果加 `provenance`：
- `command_hash`：命令代码 sha256 前 16 位（worker execute 时算）；
- `data_signature`：Stata `datasignature` 数据指纹（如 `74:12(71728):...`）。

实测：`regress` 的 structured 现在含 `provenance: {command_hash, data_signature}`。这是 agent"数值接地"的基础——每个数字能溯源到"哪条命令 + 哪个数据版本"。

**缺陷 3（t/z 判断）评估**：`e(df_r)` 存在→t 分布是 Stata 标准信号，对绝大多数命令可靠（OLS 类有 df_r、ML 类无），例外极少，暂不特殊处理，留待遇到误判案例再修。

## 21. P12 受限模式：防有害文件注入（2026-09-03）

**动机**：批判性审视缺陷 5——`stata_run` 能执行任意命令，agent 被注入后可用
`shell`/`erase`/`use "C:\\敏感"` 逃逸到文件系统，绕过 `stata_load_data` 的路径审计。

**三层防护**（`guard/restrict.py`）：
1. **shell 逃逸**：`shell`/`winexec`/`!`（一旦能执行 OS 命令，一切限制失效）；
2. **文件删除**：`erase`/`rm`；
3. **越权文件路径**：`use`/`import`/`save`/`cd`/`append`/`merge` 命令的路径，用 DataPathAuditor 审计。

**修复 mcp-for-stata 的 bysort 绕过**：命令首词提取先剥 `capture/quietly/noisily`
前缀，再剥 `by varlist:`/`bysort varlist:` 前缀。实测 `bysort foreign: shell rm -rf /`
被正确拦截（mcp-for-stata 的 validator 自己再剥一遍前缀、漏了 bysort）。

**开关**：`stata_run` 加 `restricted` 参数（省略用 config `security.restricted_mode`，
默认 false）。本地可信 agent 场景无限制，跑不可信代码时开启。

**测试**：15 个单元测试（含 bysort/capture 绕过回归）+ 端到端（restricted shell 拦截、
正常命令放行）。全量 88 测试通过。

## 22. P13 报错托底（2026-09-03）

**动机**：Stata 报错时，agent 要能拿到"原因 + 相关情况"，不只一个 rc 数字。

**现状盘点**：命令报错本已能返回 text（错误消息）+ rc + error_class；真正缺口是
**引擎级错误**（超时/崩溃）信息模糊——之前崩溃只返回 "session crashed"，不知道哪
条命令触发的。

**统一结构化错误对象**：`Envelope` 加 `error` 字段，覆盖三类：
- `command_failed`：`{kind, rc, class, message}`（如 `{kind: command_failed, rc: 111, class: not_found, message: "variable no_such_var not found"}`）
- `timeout`：超时打断（break 保状态）或 kill（仍卡死），message 带命令信息
- `crashed`：worker C 引擎崩溃，message 带"跑哪条命令时崩的"

`SessionResult` 加 `error_kind`（timeout/crashed/None）；`session.execute` 的
break 打断路径也标记 `error_kind="timeout"`（此前 break 优雅打断后 rc=1 但无标记，
agent 无法理解）。`server.py` 把 error 对象放进 `_meta` 传给 agent。

实测：命令报错 error 对象完整、超时 error_kind=timeout、崩溃/超时带命令信息。

## 23. P0 工业级加固（2026-09-03）

工业级审计 + 头脑风暴后的 P0 四件套：
- **P0-1 输出截断**：`output/truncate.py` head+tail（70/30）截断，meta 标记 truncated，防超大输出撑爆上下文。
- **P0-2 图返回 base64 ImageContent**：Envelope 加 images[(mime,bytes)]，export_graph 读文件字节，server 转 MCP ImageContent。实测 MCP 返回 ['text','image']，多模态 agent 直接看到图。
- **P0-3 会话上限/license 管理**：config `security.max_sessions`，SessionManager 超限抛 SessionLimitExceeded（工具转友好 Envelope）；修 `is_alive` 对懒会话（_proc None）视为活着 + `_reap_idle` 清理崩溃会话。实测 2 会话 + 第 3 个被拦。
- **P0-4 do-file 往返 + 结果版本化**：worker 跟踪 data_load_cmd（载入命令）+ exec_seq（执行序号），provenance 附带可复现 do-file（载入前缀 + 命令）。实测 provenance 含 command_hash/data_signature/exec_seq/do_file。对接 agent 数值接地。

P1/P2 待续：help 工具 / data_rows / 结构化日志审计 / HTTP transport / checkpoint 重放。

## 24. P1/P2 工业级增强（2026-09-03）

- **P1a stata_get_help**：findfile 定位 .sthlp + 读文件剥 SMCL（pystata 里 `help` 开 GUI 卡住，不可用）。topic 白名单校验。实测 regress 帮助返回文本。
- **P1b stata_data_rows**：读数据集前 N 行（worker sfi.Data.getAt，实测返回标量；Data.get 返回嵌套列表是坑）。agent 直接看数据而非 log。
- **P1c 结构化审计日志**：audit.py，JSONL 落 ~/.statamcp/audit.jsonl，记 tool/rc/elapsed/code_hash（哈希非原文）/error_class/truncated/reset。server 挂钩，失败静默。
- **P2a 正确性回归台**：tests/test_real_stata.py，真引擎比对已知 Stata 数值（防"悄悄给错数字"回归）；无 Stata 自动 skip。

实测踩坑：spawn 脚本必须 __main__ guard（递归 spawn 挂起）；超时杀掉的 spike 会留孤儿 worker 占 license（已 taskkill，工业级需会话看门狗防泄漏——见待续）。全量 92 测试（88 mock + 4 real）通过。

待续：会话孤儿看门狗、HTTP transport（官方 mcp.streamable_http 原生支持，无需 hack）、checkpoint 重放。

## 25. P14 会话孤儿看门狗（2026-09-03）

**问题**：主进程被强杀（taskkill /F / 崩溃 / os._exit）时，multiprocessing 的 daemon
清理不执行，worker 变孤儿继续占 Stata license。实测触发：超时杀 spike 后 tasklist 发现
残留 python 占 license。

**第一版（PID 轮询，失败）**：worker 内置 `_parent_watchdog` 周期 `_parent_alive`（ctypes
OpenProcess）检测父进程。隔离测试通过，但**真实场景失效**——Windows 快速复用 PID，
父死后的 pid 被新进程占用 → OpenProcess 返回成功 → 看门狗误判父还活着。结论：
PID 轮询在 Windows 上本质不可靠。

**第二版（Job Object，成功）**：`platform/job.py`——创建 kill-on-close 的 Job Object
（`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`），Session._start 把 worker Assign 进去。父进程
无论怎么死，OS 关闭其 job 句柄时自动终止 worker。不依赖 PID 轮询，无复用竞态。
Session._cleanup 关句柄（worker 已终止，无副作用）。非 Windows 返回 None，PID 看门狗兜底。

实测：子进程点火后 os._exit 模拟崩溃，worker 6s 内消失（license 释放）。92 测试全绿。

踩坑记录：ctypes.wintypes 无 ULONGLONG（用 c_uint64）；Job Object 结构对齐要精确否则
SetInformationJobObject 静默失败（kill-on-close 不生效）。

待续：HTTP transport（官方 mcp.streamable_http 原生支持）、checkpoint 重放。

## 26. P15 会话命令日志 + 重放原料（2026-09-03）

**动机**：命令记下来很有用——可追溯、崩溃后给 agent 重放原料、把一次分析变成可重跑脚本。

**关键设计**：日志放**主进程 Session**（不是 worker）——worker 崩溃时主进程还活着，日志才保得住。环形上限 500 条，每条 {seq, cmd, rc}。

**实现**：
- Session._journal 环形日志，execute 每次记录（含失败 rc）；
- 崩溃重置（worker 崩溃/超时杀进程/响应错乱）或重建时，SessionResult 附 `replay` = 崩溃前完整日志，供 agent 重放恢复状态；
- `stata_session_history` 工具读取（structured 含 replay_do，可直接重跑重建状态）；
- run.py 崩溃重置时 meta 带最近 30 条重放命令。

**实测**：journal 记录验证通过（3 条 seq/rc/cmd 正确）。崩溃重建后的 replay 返回，因网络 license 对强杀会话回收有延迟（多次强杀后席位滞后、新引擎 init 阻塞）未能稳定端到端复验——代码路径与 spike52（加 Job Object 前验证过的 terminate→重建）一致。

**注**：跨服务器重启的持久日志归 agent 台账层（MCP 内做内存级即可）。全量 92 测试通过。

## 27. P16 安全审计修复（2026-09-03）

逐条核验外部审计的 6 个发现并修复（104 测试通过，含 12 个审计回归）：
1. **background 绕过 restricted**（P1）：restricted 校验从"background 分支之后"提前到"之前"，后台 shell 同样拦截。
2. **session_id 未路由**（P1）：make_context 注入 manager + session_id，`_resolve_session` 真按 session_id 从 manager 路由（白名单 `^[A-Za-z0-9_-]{1,64}$`）。实测两会话隔离正确。
3. **启动失败干等 300s**（P1）：加 **worker ready 握手**（worker init 完发 ready），Session `_await_ready` 用 `_START_TIMEOUT=60s` 判启动失败（error_kind=start_failed），不再等命令超时。实测首执行含握手 0.6s。
4. **restrict 绕过**（分号/无引号路径）：解析重写——按换行+分号拆命令段、文件路径无引号也审计、危险命令整句词边界扫描（不误伤 `display "shell"`）。
5. **后台结果丢结构化/provenance**（P2）：`enrich_structured` 抽成 run/task_status 共用，后台任务现在返回 structured + provenance。
6. **URL 守卫非完整 SSRF**（P2）：补拒 localhost/127.x/*.local/云元数据域名；DNS rebinding 等仍属已知边界（需解析后校验，文档已注）。


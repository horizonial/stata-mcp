# stata-mcp 整体架构策划（v1.0，开发基准）

> 本文档是唯一的架构基准。后续所有 haiku 子 agent 的委派说明，都引用本文档的模块/接口编号，防止各写各的。
> 分工：pro 负责策划 + review + 实测；haiku 负责按委派实现。

---

## 0. 定位与原则

**定位**：面向 LLM / agent 的自研 **Stata 执行工具服务器（MCP）**。不是通用统计平台，不是从零重写——是"吸收四个开源项目的优点、补它们的缺、留好扩展位"。

**五条不可妥协的原则**：

1. **薄服务器 + 注册化**：核心只做编排，能力全是"加模块 + 注册"，避免 mcp-stata 8600 行单体。
2. **不赌第三方内部实现**：不用 fastapi-mcp 的私有 API，传输用官方 mcp SDK 表面。
3. **先验证后定稿**：每个技术选型以 spike 实测数据为准（已产出 01/02/03 三份结论）。
4. **安全审计是一等公民**：可插拔，fail-closed，但只当防线不当卖点。
5. **扩展性 = 抽象边界清晰**：凡是"未来会变/会增加"的东西，一律接口化 + 注册表。

---

## 1. 需求分析

### 1.1 当前需求（钉死）
- 平台：Windows 11（中文）；Stata 18 MP + pystata；Python 3.12。
- 服务自建多步 agent：持久会话 + 结构化结果 + 可靠中断。
- 独立可演示，可被任何 MCP 客户端（Claude Code 等）接入。
- 人力少，要能长期演进。

### 1.2 前瞻需求（扩展性要为之预留）
- **其他统计后端**：批处理 Stata（无 pystata 的机器）、R、Python/SAS（同类工具服务器）。
- **多客户端 / 多会话**：同时被 IDE 扩展 + 多个 agent 连接。
- **多用户**：共享一台机器，会话隔离、资源配额、license 并发上限。
- **长任务 / 流式**：bootstrap 等跑很久，需要后台任务 + 进度。
- **更多数据格式**：dta/csv/xlsx 之外，spss/sav、parquet。
- **可观测性**：日志、诊断、审计留痕。

这些前瞻需求**现在不实现**，但每个都对应一个抽象接口，见 §5。

---

## 2. 四项目优点吸收 + 缺点改正（完整映射）

| 来源 | 吸收的优点 | 改正的缺点（我们不做） |
|---|---|---|
| tmonk/mcp-stata | 进程级会话隔离；out-of-band break（`sfi.breakIn` 独立线程）；持久日志偏移流式；Windows 图导出绕行；clear 后重载启动 do | 不做 8600 行单体；研究编排不进 server；避开它的事件循环 bug、local 作用域 rc 恒 0 bug |
| SepineTam/mcp-for-stata | **guard 静态展开 + fail-closed**；**DataPathAuditor 集中路径审计**；**分层配置（安全区用户优先）**；**隐私日志（路径哈希）**；ado 安装三层防御 | 修正 bysort/by 前缀绕过守卫的 bug；Windows 执行层半成品重做；不做无状态批量主线 |
| haoyu-haoyu/stata-ai-fusion | PipeSession 哨兵+日志追踪思想；SessionManager 并发模型；结果提取器（矩阵解析） | 不用 pexpect（Windows 无 PTY）；修 run_command 不截断的问题 |
| hanlulong/stata-mcp | 命名日志与用户日志共存；log 字节偏移流式；多 worker 隔离；会话自愈 | 不 monkey-patch fastapi-mcp；不做单会话无锁全局；图路径纳入返回 |

**我们补的四个共同缺（= 差异化）**：① 结构化结果 ② 中文 Windows 编码 ③ Windows 进程管理 ④ 可扩展的安全审计（见 §4）。

---

## 3. 关键决策（已定 + 实测依据）

| # | 决策 | 依据 |
|---|---|---|
| D1 | 默认驱动 pystata 内嵌，抽象 `ExecutionBackend` | 保状态 + 读写 Stata 内存；可切批处理/R |
| D2 | 会话 MVP 单进程 → 二期按需拆 worker 子进程 | 隔离是优点但不提前付复杂度 |
| D3 | **输出捕获 = 进程内 sys.stdout 交换**（按调用、串行锁） | **实测（spike03）**：pystata 输出走 sys.stdout，进程内完整捕获；绕开文件锁/log close _all |
| D4 | 编码探测链 UTF-8→GBK→latin-1 | **实测（spike02）**：Stata 18 text log 是 UTF-8，GBK 作老文件回退 |
| D5 | rc 用**全局 scalar** 捕获 | **实测（spike02）**：rc=111 稳定拿到；避开 mcp-stata 的 local 作用域 bug |
| D6 | 传输 stdio 起步，工具层与传输解耦 | 简单够用；需要时加 HTTP |
| D7 | 返回信封 `Envelope`（见 §6.2） | 统一 text/structured/rc/error_class/graphs |
| D8 | 安全审计分层可插拔（§4） | 吸收 mcp-for-stata + 修 bug |

---

## 4. 安全审计设计（重点，吸收 + 改进）

分层，从外到内，每层独立可插拔、可开关（`Guard` 接口见 §6.4）：

### L1 参数校验层（每个工具入参）
- 每个嵌入 Stata 命令的字符串都做**白名单正则**：变量名、包名、文件名、路径、URL、结果名。
- 吸收自 stata-ai-fusion（它每处都做了）。**我们统一成 `validate` 装饰器**，不是散落各处。

### L2 命令安全层（静态展开 + fail-closed）
- 吸收 mcp-for-stata 的 `parse_dofile`：把 do 文件**展开成 Stata 真实执行形式**（去注释/`she/**/ll` 混淆、缩写展开 `u→use`、macro 追踪、静态循环展开）再查危险命令。
- **修正其 bug**：危险命令检测必须复用 parse_dofile 的命令抽取结果（正确处理 `bysort`/`by varlist:` 前缀），不自己再剥一遍前缀。
- 危险命令黑名单：`shell/!、winexec、erase、do(外部)、copy…replace、net/ssc/github(可选)` 等。
- fail-closed：静态解不了的 → 拒绝（可配置为"仅提示"）。

### L3 数据路径层（集中审计）
- 吸收 `DataPathAuditor`：本地路径边界（限定在 data/ 与授权目录）+ URL 规则（默认 HTTPS、拒 IP 字面量、域名 allowlist）。
- **默认开启**（修正 mcp-for-stata 默认关闭导致 SSRF 的问题）。

### L4 运行资源层
- RAM / 时间 / 进程树监控。**修 mcp-for-stata 的 Windows bug**：超时/超限清理用 `taskkill /T /F` 杀进程树，监控统计进程树内存总和（不是 cmd.exe）。
- 吸收 hanlulong 的会话自愈：卡死判定 + 原地重启。

### 配置层（支撑以上）
- 分层配置：环境变量 > 配置文件 > 默认；**安全区用户级优先**（防止项目级配置覆盖用户安全策略，吸收 mcp-for-stata）。
- 隐私日志：路径/URL 哈希后落日志（吸收 mcp-for-stata 的 `_diagnostic_logging`）。

---

## 5. 扩展性设计（重点，前瞻）

凡"未来会变"的都接口化。核心抽象如下（接口签名见 §6）：

| 抽象 | 未来可扩展成 | 现在实现 |
|---|---|---|
| `ExecutionBackend` | R / SAS / 批处理 Stata / 远程 | PystataBackend |
| `Transport` | HTTP/SSE、多客户端 | stdio |
| `SessionManager` | 多 worker、多用户、配额 | 单进程 |
| `Tool` + registry | 任意新工具 | run |
| `ResultParser` + registry | 任意命令的结果 schema | （P3 起）reg |
| `Guard` + chain | 新审计规则、不同后端不同规则 | L1–L4 |
| `Encoder` | 任意编码 | UTF-8→GBK→latin-1 |
| `OutputSink` | 流式、截断、转文件 | 内存缓冲 + 截断 |

**扩展成本目标**：加一个新能力 = 加一个模块文件 + 一行注册，不修改 core。

---

## 6. 关键接口定义（haiku 子 agent 照此实现）

### 6.1 ExecutionBackend
```python
class ExecutionBackend(Protocol):
    def init(self) -> None: ...
    def execute(self, code: str, *, timeout: float | None = None) -> ExecutionResult: ...
    def interrupt(self) -> None: ...          # sfi.breakIn，独立线程
    def close(self) -> None: ...
    def capabilities(self) -> dict: ...       # {"struct_results": bool, "graphs": bool, ...}
```

### 6.2 Envelope（工具统一返回）
```python
@dataclass
class Envelope:
    text: str                      # 清洗后文本
    structured: dict | None        # 结构化结果（P3）
    rc: int
    error_class: str | None        # "syntax"|"sample"|"convergence"|"not_found"|None
    graphs: list[str]              # 图路径
    meta: dict                     # 耗时、截断标记等
```

### 6.3 Tool + registry
```python
@dataclass
class Tool:
    name: str
    input_schema: dict             # MCP JSON schema
    handler: Callable[[dict, Session], Envelope]
# registry: TOOLS = {} ; register(name) 装饰器
```

### 6.4 Guard + chain
```python
class Guard(Protocol):
    def check(self, code: str, ctx: GuardContext) -> GuardResult: ...  # allowed/denied+reason
# 链式：L1→L2→L3→L4，任一层 deny 即拒绝（可配置哪些层提示不阻断）
```

### 6.5 ResultParser + registry
```python
class ResultParser(Protocol):
    command_types: tuple[str, ...]  # 匹配哪个命令
    def parse(self, text: str, backend) -> dict: ...
# registry: 按命令类型路由到 parser（P3 起）
```

### 6.6 Session
```python
class Session:
    backend: ExecutionBackend
    state: dict                     # 会话级元数据
    def execute(self, code, **kw) -> Envelope: ...   # 内部串行锁 + guard + 捕获
```

---

## 7. 目录结构（规划）

```
stata-mcp/
├── pyproject.toml
├── ARCHITECTURE.md / DESIGN.md
├── spikes/                 # 已完成 01/02/03
├── src/stata_mcp/
│   ├── server.py           # 组装 + stdio 入口（薄）
│   ├── envelope.py         # Envelope 定义
│   ├── tools/              # Tool 注册表 + run.py 等
│   ├── stata/
│   │   ├── backend.py      # ExecutionBackend 协议
│   │   ├── pystata_backend.py
│   │   └── discovery.py
│   ├── output/
│   │   ├── capture.py      # stdout 交换捕获
│   │   ├── encoding.py     # 探测链
│   │   ├── smcl.py         # SMCL 清理 + 回显去噪
│   │   └── errors.py       # error_class 分类
│   ├── results/            # ResultParser + regression.py（P3）
│   ├── guard/              # L1-L4（P4，先 L1/L3）
│   ├── session/            # Session / SessionManager
│   └── config.py
└── tests/
```

---

## 8. 开发路线（成本优先：每批 haiku 实现 + pro review/实测）

| 批 | 内容 | 产出 | 验收（pro 实测） |
|---|---|---|---|
| P2 | 骨架 + pystata_backend + capture + encoding + smcl + run 工具 + server | 可 import 的包，`stata_run` 端到端 | 客户端调 `stata_run("display 2+2")` 拿回文本 |
| P3 | 结构化结果（reg schema）+ 错误分类 + Envelope 落全 | reg→JSON | 真实 `reg` 出结构化 JSON |
| P4 | guard L1/L3（参数校验 + 数据路径） | 安全基础层 | 注入/越界用例被拦 |
| P5 | 会话自愈 + 中断 + 图 + 更多工具 | 健壮版 | 中断长命令、导出图 |
| P6 | 扩展性压力测试（模拟加一个"假后端"验证抽象） | 验证扩展位真的可用 | 加一个 stub backend 不改 core |

---

## 9. 风险与未决

- **license 9/9 到期**：用户明确"不管"，本地有 Stata 即可；只影响连续性，不阻塞架构。
- **stdout 交换的并发**：单引擎内必须串行（锁）。多会话 = 多进程，各自交换，天然隔离。
- **回显去噪**：Stata 会回显命令，需在 smcl.py 里做可靠去噪（spike 待补）。
- **图导出在 Windows 的绕行**：P5 时实测确认是走 `graph save`+外部批处理，还是 pystata 内直接可用。
- **haiku 写代码质量**：靠 pro review + 实测兜底；接口定义已写到 §6，haiku 只需填实现。

---

## 10. 委派纪律（给后续每一批的固定约定）

1. 每个 haiku 委派说明必须**精确到文件**，并引用本文档 §6 的接口签名，禁止自行发明新抽象。
2. 禁止引入 AGPL 代码或从克隆的四个仓库直接 copy（用结论、用思路，不抄实现）。
3. 依赖只允许：`mcp`（官方）。其他（fastmcp/fastapi 等）一律不加，除非 pro 批准。
4. 每批产出后，pro 先 review 接口是否符合 §6，再实测；不过不进入下一批。

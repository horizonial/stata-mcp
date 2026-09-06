"""分层配置读取 + Stata 定位（ARCHITECTURE.md §4 配置层，P4）。

P2 只有 STATA_HOME 环境变量覆盖；P4 引入两层 TOML 配置文件，优先级：

    普通配置：环境变量 > 项目配置 ./.statamcp/config.toml
              > 用户配置 ~/.statamcp/config.toml > 代码默认
    [security] 段：环境变量 > 用户配置 > 项目配置 > 代码默认

为什么 security 段要"用户优先于项目"（吸收 mcp-for-stata 的核心设计，自研实现）：
- 安全策略是"用户对这台机器/自己的数据的总体约束"，属于用户级意图；
- 项目配置文件可能随仓库被拷贝/被塞进宽松规则（或把 allowed_data_dirs 指向
  项目自己目录），若项目级能覆盖用户级，等于任何拿到的项目都能放松用户的安全边界；
- 环境变量仍最高：它代表"本次启动显式注入"，是部署者最外层、最可信的防线。

为什么用标准库 ``tomllib`` 而不加依赖：Python 3.11+ 自带，只读 TOML 够用；
P4 明确不写配置文件（写配置留未来），所以不需要 tomli-w 之类的第三方。
"""
from __future__ import annotations

import copy
import os
import re
import tomllib
from typing import Callable

_DEFAULT_STATA_HOME = r"C:\Program Files\Stata18"

# 代码默认值（最低层）。当前只有 [security] 段真正被消费；
# 以后加普通配置段（工具默认、日志等级等）直接往这里加键即可。
_DEFAULTS: dict = {
    "security": {
        # 额外授权的数据目录（绝对路径）。空 = 未额外授权 → DataPathAuditor
        # 在 allowed_dirs 为空时一律拒绝本地路径（fail-closed，见 guard/data_path.py）。
        "allowed_data_dirs": [],
        # URL 守卫默认开启。为什么默认 True：ARCHITECTURE §4 L3 明确
        # "修正 mcp-for-stata 默认关闭导致 SSRF"，开关只允许显式关。
        "enable_url_guard": True,
        # 域名白名单。空 = 不做 host 白名单限制（仍强制 https、拒 IP/userinfo）。
        "allowed_hosts": [],
        # 受限模式（P12）：拦截 shell 逃逸/文件删除/越权路径。默认关——本地可信
        # agent 场景无限制；跑不可信代码时显式开启，防有害文件注入。
        "restricted_mode": False,
        # 最大并发会话数（P0-3）：每个会话 = 一个 Stata worker = 一个 license 席位。
        # 0 = 不限制。多用户/共享 license 场景应设上限，防 license 耗尽。
        "max_sessions": 0,
    },
}

def _parse_bool(value: str) -> bool:
    """字符串 → bool：空串 / 0 / false / no / off 视为 False，其余 True。"""
    return value.strip().lower() not in {"", "0", "false", "no", "off"}


def _parse_list(value: str) -> list[str]:
    """字符串 → list：逗号和分号都当分隔符（Windows 路径分隔是 ;，域名用 , 自然）。"""
    return [part.strip() for part in re.split(r"[;,]", value) if part.strip()]


# 环境变量覆盖表：变量名 → (段, 键, 字符串→值的解析函数)。
# 为什么用显式小表而不是"扫 STATAMCP_ 前缀自动映射"：
# - 自动映射要把字符串解析成任意嵌套类型（bool/list/...），易踩类型坑；
# - 显式表每行声明键名和类型解析，加一个新配置项只加一行，可单测、类型安全。
# 环境变量的值总是字符串：bool/list 需要解析（TOML 文件里是原生类型，不需要）。
_ENV_OVERRIDES: dict[str, tuple[str, str, Callable[[str], object]]] = {
    "STATAMCP_DATA_DIRS": ("security", "allowed_data_dirs", _parse_list),
    "STATAMCP_ENABLE_URL_GUARD": ("security", "enable_url_guard", _parse_bool),
    "STATAMCP_ALLOWED_HOSTS": ("security", "allowed_hosts", _parse_list),
    "STATAMCP_RESTRICTED": ("security", "restricted_mode", _parse_bool),
    "STATAMCP_MAX_SESSIONS": ("security", "max_sessions", int),
}


def _read_file(path: str) -> dict:
    """读一个 TOML 配置文件为 dict；文件缺失/不可读/损坏 → 静默回退 {}。

    为什么静默回退：配置是可选覆盖层；某个文件坏了不应让服务起不来，
    缺了它就等于"这一层没提供配置"。TOML 根必须是 dict，否则也当没配。
    """
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _deep_merge(base: dict, override: dict) -> dict:
    """把 override 递归合并进 base（就地修改并返回 base）。

    规则：dict 值递归合并；list / 标量由 override 整体替换（覆盖，不拼接）。
    这样"项目级给 security.enable_url_guard"只会覆盖这一个键，不会抹掉其它 security 键。
    """
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _user_config_path() -> str:
    """用户级配置：``~/.statamcp/config.toml``。"""
    return os.path.join(os.path.expanduser("~"), ".statamcp", "config.toml")


def _project_config_path() -> str:
    """项目级配置：``<当前工作目录>/.statamcp/config.toml``。

    为什么锚定 cwd 而不是向上找仓库根：本服务是单工作目录服务器（跑在数据集目录），
    用 cwd 语义最直接；将来要多项目同时服务再改成"向上探测最近 .statamcp"。
    """
    return os.path.join(os.getcwd(), ".statamcp", "config.toml")


def _env_layer(environ) -> dict:
    """把环境变量层解析成嵌套 dict（``{"security": {"enable_url_guard": False}}``）。"""
    out: dict = {}
    for name, (section, key, parse) in _ENV_OVERRIDES.items():
        if name in environ:
            out.setdefault(section, {})[key] = parse(environ[name])
    return out


def load_config(
    *,
    project_path: str | None = None,
    user_path: str | None = None,
    environ: dict | None = None,
) -> dict:
    """合并各层配置，返回普通嵌套 dict（调用方可直接 ``cfg["security"]``）。

    可 override 的三个参数都是测试缝：
    - ``project_path`` / ``user_path`` 指向 TOML 文件（缺省用真实路径），
      测试传临时文件即可不碰真实 HOME/cwd；
    - ``environ`` 注入环境变量字典（缺省用 os.environ），测试可隔离。

    合并顺序严格按模块 docstring 的优先级执行；返回值是副本，修改它不影响模块状态。
    """
    if environ is None:
        environ = os.environ
    if project_path is None:
        project_path = _project_config_path()
    if user_path is None:
        user_path = _user_config_path()

    user_cfg = _read_file(user_path)
    project_cfg = _read_file(project_path)

    # 普通配置：default → user → project（后盖前），project 最高（除 security 外）。
    merged = _deep_merge(copy.deepcopy(_DEFAULTS), user_cfg)
    merged = _deep_merge(merged, project_cfg)

    # [security] 特例重排：default(sec) → project(sec) → user(sec)。
    # 上面普通合并里 project 的 security 已盖过 user 的，这里按"user 优先"重算一遍
    # 覆盖回去，使安全区的 user > project。
    security = copy.deepcopy(_DEFAULTS.get("security", {}))
    security = _deep_merge(security, project_cfg.get("security", {}))
    security = _deep_merge(security, user_cfg.get("security", {}))
    merged["security"] = security

    # 环境变量恒最高，最后盖。
    merged = _deep_merge(merged, _env_layer(environ))
    return merged


def get(config: dict, section: str, key: str, default=None):
    """取 ``config[section][key]``；段或键缺失返回 default。"""
    return config.get(section, {}).get(key, default)


def get_security(config: dict, key: str, default=None):
    """取 ``[security]`` 段合并后的值（已含 user > project 特例）。"""
    return config.get("security", {}).get(key, default)


def stata_home() -> str:
    """Stata 根目录；环境变量 STATA_HOME 可覆盖（剥掉常见的引号包裹）。"""
    return os.environ.get("STATA_HOME", _DEFAULT_STATA_HOME).strip().strip('"')


def utilities_dir() -> str:
    """pystata/sfi 所在目录：``<stata_home>/utilities``。"""
    return os.path.join(stata_home(), "utilities")


__all__ = [
    "load_config",
    "get",
    "get_security",
    "stata_home",
    "utilities_dir",
]

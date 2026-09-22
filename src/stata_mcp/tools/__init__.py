"""工具注册表（ARCHITECTURE.md §6.3）。

用法：
    @register("stata_run", input_schema={...})
    def stata_run(arguments: dict, ctx=None) -> Envelope:
        ...

加一个新工具 = 新增一个模块 + 一行注册，不修改 core/server。
"""
from __future__ import annotations

from dataclasses import dataclass
from inspect import getdoc
from typing import Callable, Optional

from ..envelope import Envelope

# handler 契约：Callable[[dict, Any], Envelope]
# 第一参为工具入参 dict；第二参 P2 传 None（未来传 Session），handler 内可忽略。
Handler = Callable[..., Envelope]


@dataclass
class Tool:
    name: str
    input_schema: dict  # MCP JSON Schema
    handler: Handler
    description: Optional[str] = None


TOOLS: dict[str, Tool] = {}


def register(name: str, input_schema: dict):
    """把 handler 注册进 TOOLS；description 缺省取 handler 的 docstring。"""

    def decorator(fn: Handler) -> Handler:
        if name in TOOLS:
            raise ValueError(f"duplicate tool registration: {name!r}")
        TOOLS[name] = Tool(
            name=name,
            input_schema=input_schema,
            handler=fn,
            description=getdoc(fn),
        )
        return fn

    return decorator


# 自动导入本目录下的工具模块，让各自的 @register 生效。
# 扩展成本目标（§5）：加一个新工具 = 放一个新模块文件，无需改 core。
import importlib as _importlib
import pkgutil as _pkgutil

for _mod in _pkgutil.iter_modules(__path__):
    _importlib.import_module(f"{__name__}.{_mod.name}")
globals().pop("_mod", None)
del _importlib, _pkgutil

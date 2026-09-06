"""测试包：让 ``python -m unittest`` 在未安装本包时也能 import ``stata_mcp``。

为什么在 __init__ 里把仓库 src 目录插进 sys.path：
- 项目是 src-layout（pyproject [tool.setuptools.packages.find] where=["src"]），
  未 ``pip install -e .`` 时 ``import stata_mcp`` 会失败；
- 这样对齐委派说明的运行命令 ``python -m unittest tests.test_guard -v``，
  在干净 clone 里直接可跑，不依赖先安装。已安装时多插一个路径也无副作用。
"""
from __future__ import annotations

import os
import sys

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

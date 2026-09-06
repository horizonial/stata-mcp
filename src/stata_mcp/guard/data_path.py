"""L3 数据路径审计：DataPathAuditor（ARCHITECTURE.md §4 L3，P4）。

吸收 mcp-for-stata 的"集中路径审计"思路（本地路径边界 + URL 规则），自研实现：
- 本地路径：归一后必须落在任一授权目录内（防目录穿越 / 符号链接逃逸 / 盘符绕过）；
- URL：默认只放行 https、拒绝 IP 字面量与 userinfo、可选域名白名单（防 SSRF）；
- 全部 fail-closed：任何解析失败 / 越界 / 规则不满足都返回 False。

为什么统一成一个类而不是散落各处判断：ARCHITECTURE §4 说 L3 要"集中审计"，
工具层只调 ``auditor.check(path_or_url)`` 一个入口即可，规则收敛在一处。
"""
from __future__ import annotations

import ipaddress
import os
import re
from urllib.parse import urlsplit

# scheme:// 前缀判定（Windows 路径 C:\ 与 UNC \\ 都不含 ://，不会被误判成 URL）
_SCHEME_PREFIX = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")


def _is_ip_literal(host: str) -> bool:
    """host 是否为 IP 字面量（IPv4 / IPv6）。SSRF 要挡的就是直连内网 IP。"""
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _normalize_dir(d: str) -> str:
    """把授权目录归一成"可比较形态"：绝对 + realpath + normcase。

    realpath 会解析符号链接 / junction / ``..``，保证"目录边界"是物理路径而非
    词法路径（否则符号链接可把文件引到授权目录外还通过检查）；
    normcase 统一大小写与分隔符，让 Windows 的 C:\\Foo 与 c:\\foo 视为同一目录。
    """
    expanded = os.path.expanduser(d)
    return os.path.normcase(os.path.realpath(os.path.abspath(expanded)))


class DataPathAuditor:
    """集中数据路径审计（fail-closed）。

    参数：
    - ``allowed_dirs``：授权目录绝对路径列表；空列表 = 本地路径一律拒绝。
    - ``enable_url_guard``：URL 规则开关；False 时 URL 一律放行（显式关闭才生效）。
    - ``allowed_hosts``：可选域名白名单；空 = 不做 host 白名单限制。
    """

    def __init__(
        self,
        allowed_dirs: list[str] | None = None,
        enable_url_guard: bool = True,
        allowed_hosts: list[str] | None = None,
    ) -> None:
        if allowed_dirs is None:
            allowed_dirs = []
        if allowed_hosts is None:
            allowed_hosts = []
        self.enable_url_guard = bool(enable_url_guard)

        # host 白名单统一小写存储；单个 host 含非法字符/空 → 丢弃（fail-closed 更保守）
        self._allowed_hosts = {
            h.strip().lower() for h in allowed_hosts if isinstance(h, str) and h.strip()
        }

        # 授权目录归一后存储；归一失败的目录项直接丢弃（它本来就不可信）
        self._allowed_dirs: list[str] = []
        for d in allowed_dirs:
            if not isinstance(d, str) or not d.strip():
                continue
            try:
                self._allowed_dirs.append(_normalize_dir(d))
            except (OSError, ValueError):
                continue

    # ---- 本地路径 -----------------------------------------------------------

    @staticmethod
    def _is_inside(normalized_child: str, normalized_parent: str) -> bool:
        """child 是否落在 parent 之内（两者都须已 normcase 归一）。

        用 ``commonpath`` 而非字符串 startswith：
        - startswith("C:\\data") 会把 C:\\data2 误判为在 C:\\data 内；
        - commonpath 跨盘符抛 ValueError → 捕获返回 False（不同盘永远不越界也算对）。
        """
        try:
            return os.path.commonpath([normalized_child, normalized_parent]) == normalized_parent
        except ValueError:
            return False

    def check_local_path(self, path: str) -> bool:
        """本地路径是否落在任一授权目录内。不可解析 / 无授权目录 → False。"""
        if not isinstance(path, str) or not path:
            return False
        if not self._allowed_dirs:
            # 没配任何授权目录：无可放行目标，fail-closed
            return False
        try:
            normalized = os.path.normcase(os.path.realpath(os.path.abspath(path)))
        except (OSError, ValueError):
            # 含 NUL 等不可解析输入 → 拒绝
            return False
        return any(self._is_inside(normalized, d) for d in self._allowed_dirs)

    # ---- URL ----------------------------------------------------------------

    def check_url(self, url: str) -> bool:
        """URL 是否满足守卫规则。守卫关闭时一律放行；否则 https + 拒 IP/userinfo
        + host 白名单；解析失败 → False。"""
        if not self.enable_url_guard:
            return True
        if not isinstance(url, str) or not url.strip():
            return False
        try:
            parts = urlsplit(url)
            scheme = parts.scheme.lower()
            # hostname 已去 userinfo / 端口 / IPv6 方括号，且已小写
            host = parts.hostname
            if scheme != "https":
                return False
            if not host:
                return False
            # 拒绝 userinfo（user:pass@host）：凭据不该出现在数据 URL，且常是钓鱼/代理混淆
            if parts.username is not None or parts.password is not None:
                return False
            # 拒绝 IP 字面量：SSRF 的核心入口是"让服务器去连内网 IP"
            if _is_ip_literal(host):
                return False
            # 拒绝 localhost / 本机 / 内网域（P16：补 SSRF 常见绕过）
            #   localhost、127.x、[::1]、*.local（mDNS 内网）、常见云元数据域名。
            if host == "localhost" or host.endswith(".local"):
                return False
            if host.startswith("127.") or host == "::1":
                return False
            if host in ("metadata.google.internal", "metadata.azure.internal",
                        "169.254.169.254.nip.io", "metadata"):
                return False
            # 可选域名白名单：host 精确匹配或以 .allowed 结尾（子域匹配）
            if self._allowed_hosts:
                if not any(
                    host == allowed or host.endswith("." + allowed)
                    for allowed in self._allowed_hosts
                ):
                    return False
            return True
        except ValueError:
            # userinfo/port 含非法转义等解析异常 → 拒绝
            return False

    # ---- 自动分流 -----------------------------------------------------------

    def check(self, path_or_url: str) -> bool:
        """自动区分 URL 与本地路径分别审计。

        判定依据：形如 ``scheme://`` 视为 URL；否则按本地路径。Windows 路径
        （``C:\\...``）与 UNC（``\\\\host\\share``）都不含 ``://``，走本地路径分支。
        """
        if not isinstance(path_or_url, str):
            return False
        if _SCHEME_PREFIX.match(path_or_url):
            return self.check_url(path_or_url)
        return self.check_local_path(path_or_url)


__all__ = ["DataPathAuditor"]

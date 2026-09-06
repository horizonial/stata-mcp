"""Windows Job Object：父死子亡的可靠机制（P14 防孤儿 license 泄漏）。

为什么不用 PID 轮询（worker.py 的 _parent_watchdog）：Windows 会快速复用 PID，
父进程死后 pid 可能被新进程占用，OpenProcess 对它返回成功 → 看门狗失效（实测）。

Job Object 是 OS 级保证：父进程创建 job 并把子进程 Assign 进去，设置
KILL_ON_JOB_CLOSE——当父进程退出（无论正常还是被 taskkill /F / 崩溃），OS 关闭
父进程持有的 job 句柄，自动终止 job 内所有进程。不依赖 PID 轮询，无复用竞态。

仅 Windows 有效；非 Windows 返回 None（worker 的 PID 看门狗兜底）。
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
        ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.POINTER(wintypes.ULONG)),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class JobObject:
    """一个 kill-on-close 的 Windows Job Object。"""

    def __init__(self) -> None:
        self._handle = None
        if os.name == "nt":
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.CreateJobObjectW(None, None)
            if handle:
                info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
                info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                kernel32.SetInformationJobObject(
                    handle,
                    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                    ctypes.byref(info),
                    ctypes.sizeof(info),
                )
                self._handle = handle

    def assign(self, pid: int) -> None:
        """把 pid 进程 Assign 进 job（成功即：父死 → 该进程被 OS 杀）。"""
        if self._handle is None:
            return
        kernel32 = ctypes.windll.kernel32
        PROCESS_SET_QUOTA = 0x0100
        PROCESS_TERMINATE = 0x0001
        h = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, int(pid))
        if h:
            kernel32.AssignProcessToJobObject(self._handle, h)
            kernel32.CloseHandle(h)

    def close(self) -> None:
        if self._handle is not None:
            ctypes.windll.kernel32.CloseHandle(self._handle)
            self._handle = None


def create() -> JobObject:
    return JobObject()

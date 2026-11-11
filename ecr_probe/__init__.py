"""ECR Probe —— 易启学课堂协议的静态分析产物与本机探测台。

这个包只做两件事：
1. 把从 electronic-classroom.exe 里读出来的命令目录整理成可用的数据；
2. 在本机找到那个服务端、连上去、按目录发报文。

所有出站连接都经过 guard，只允许本机地址。
"""

from __future__ import annotations

__version__ = "0.1.0"

from .commands import ALL_COMMANDS, GROUPS, build_request
from .guard import NonLocalError, guard_target

__all__ = [
    "ALL_COMMANDS",
    "GROUPS",
    "NonLocalError",
    "__version__",
    "build_request",
    "guard_target",
]

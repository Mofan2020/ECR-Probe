"""打包入口。

PyInstaller 需要的是一个普通脚本，不能用 `python -m ecr_probe` 那种形式。
这个文件只做一件事：把控制权交给 ecr_probe.__main__.main()。
"""

from __future__ import annotations

import multiprocessing
import sys

from ecr_probe.__main__ import main

if __name__ == "__main__":
    # onefile 打包后如果没有这句，任何 fork 出去的子进程都会重新执行整个程序
    multiprocessing.freeze_support()
    sys.exit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""无窗口启动器：让计划任务跑 bat 时不闪黑窗口。

做法：用 pythonw.exe（本脚本自身无控制台）拉起 cmd.exe，并带
subprocess.CREATE_NO_WINDOW 标志，子进程同样不分配控制台，
因此桌面上不会出现任何窗口，也不会闪一下。

用法：
    pythonw.exe run_hidden.py "D:\\项目目录\\run_xxx.bat"
"""
from __future__ import annotations

import subprocess
import sys

# Win32 CREATE_NO_WINDOW：子进程不新建控制台窗口
CREATE_NO_WINDOW = 0x08000000


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: run_hidden.py <bat 路径>")
        return 1
    target = sys.argv[1]
    try:
        p = subprocess.run(
            ["cmd.exe", "/c", target],
            creationflags=CREATE_NO_WINDOW,
        )
        return p.returncode
    except Exception as e:
        # 无控制台时没有地方打印，退出码留给任务计划程序看
        print(f"启动失败: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

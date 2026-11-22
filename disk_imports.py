# -*- coding: utf-8 -*-
"""冻结环境下，让项目自己的 .py **优先从磁盘**加载。

为什么需要这个：PyInstaller 把 `common.py`、`dashboard.py` 这些一起冻进了 exe。
之后改了台账口径、改了签到逻辑，只要没重建 exe，跑的就还是打包那天的代码。
这个坑已经踩过三次——
  1. `index.html` 被 import 时读进内存，改前端不生效；
  2. `common.py` 被冻进 exe，界面文字是新的、数字是旧的；
  3. `cmcc_xunbao.py` 改了逻辑，控制台里还是旧的。
每次都是在某个函数里单独打补丁（`_common_for_awards`、`_index_html_path`），
补丁越多越容易漏。这里改成一次性解决：装一个 meta_path 查找器，
名字命中的模块一律从 exe 所在目录的 .py 读，读不到再退回 exe 内的冻结副本。

设计约束：
  · 只接管**项目根目录里真实存在同名 .py** 的模块，绝不篡改标准库或第三方包；
  · 源码运行（非 frozen）时完全不介入，避免开发期行为和打包后不一致；
  · 已经 import 过的同名模块会先从 sys.modules 里摘掉，保证拿到的是磁盘版。
"""

import importlib.util
import sys
from pathlib import Path

__all__ = ["install", "is_active"]

_installed = None


class _DiskProjectFinder:
    """把 `import xxx` 指向 项目根/xxx.py 的查找器。"""

    def __init__(self, base: Path):
        self.base = base
        # 只认项目根目录下的顶层 .py，避免误伤 stdlib / requests / webview
        self.names = {p.stem for p in base.glob("*.py") if p.stem.isidentifier()}
        # 别把「安装查找器的人」自己摘掉：install() 会清理同名模块，
        # 一旦清掉自己，后续 is_active() 就是另一个实例，状态全丢。
        self.names.discard("disk_imports")

    def find_spec(self, fullname, path=None, target=None):
        if "." in fullname or fullname not in self.names:
            return None
        src = self.base / (fullname + ".py")
        if not src.is_file():
            return None
        return importlib.util.spec_from_file_location(fullname, str(src))


def install(base=None):
    """装好磁盘优先查找器。非冻结环境直接返回 None（不做任何改动）。"""
    global _installed
    if _installed is not None:
        return _installed
    if not getattr(sys, "frozen", False):
        return None

    if base is None:
        base = Path(sys.executable).resolve().parent
    base = Path(base)
    if not base.is_dir():
        return None

    finder = _DiskProjectFinder(base)
    if not finder.names:
        # exe 被单独拷到别处、旁边没有 .py：保持原样，别把程序搞崩
        return None

    # 先摘掉启动阶段可能已经导入的冻结副本，再让查找器排在最前
    for name in list(sys.modules):
        if "." not in name and name in finder.names:
            del sys.modules[name]
    if str(base) not in sys.path:
        sys.path.insert(0, str(base))
    sys.meta_path.insert(0, finder)

    _installed = finder
    return finder


def is_active() -> bool:
    return _installed is not None

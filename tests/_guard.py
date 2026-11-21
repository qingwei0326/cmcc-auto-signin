# -*- coding: utf-8 -*-
"""测试守卫：把全局设置指到临时文件，测试绝不能碰用户的真实配置。

背景（今天踩到过）：用户在「设置」页配了真的 PushPlus 令牌，而
`common.notify` 会回落到全局配置。测试里有一两条用例直接调真实 notify，
于是**真的往用户手机上推了消息**（还把「没配就不发」的断言冲掉了）。

所以整个测试会话统一把 `common.GLOBAL_SETTINGS_FILE` 指向临时目录：
全局配置一律当空的，测试既不会误发推送，也不会受用户真实配置影响。

注意：每个测试模块都要 `import _guard`（本文件所在目录先加进 sys.path），
否则 `python -m unittest discover -s tests` 与 `python -m unittest tests.x`
两种跑法里至少有一种不会执行到这里。
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 第二道保险：即使有用例忘了隔离配置，notify 也只走流程、不真发消息。
os.environ["CMCC_NOTIFY_DRY"] = "1"

try:
    import common as _common
except Exception:        # pragma: no cover - common 导入失败时不要挡住测试
    _common = None

if _common is not None:
    _TMP = tempfile.mkdtemp(prefix="cmcc_test_")
    _common.GLOBAL_SETTINGS_FILE = Path(_TMP) / "settings.json"

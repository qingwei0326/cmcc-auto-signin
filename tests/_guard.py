# -*- coding: utf-8 -*-
"""各测试模块须在导入应用前加载本守卫，隔离持久化文件并禁止写项目数据。"""
from functools import wraps
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

_ROOT = Path(__file__).resolve().parent.parent
_PROTECTED_DIRS = {"gui_data", "configs", "configs_fj"}
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_TEMP = tempfile.TemporaryDirectory(prefix="cmcc_test_")
_DATA = Path(_TEMP.name)


def _check_write(path):
    if not isinstance(path, (str, bytes, os.PathLike)):
        return
    resolved = Path(os.fsdecode(path)).resolve()
    for candidate in (Path(os.path.abspath(os.fsdecode(path))), resolved):
        if not candidate.is_relative_to(_ROOT):
            continue
        parts = candidate.relative_to(_ROOT).parts
        if not parts:
            continue
        name = parts[0].lower()
        if name in _PROTECTED_DIRS or (len(parts) == 1 and
                                      name.startswith(".cmcc_") and
                                      "cache" in name and name.endswith(".json")):
            raise PermissionError(f"测试禁止写入项目数据：{candidate}")


def _audit(event, args):
    if event == "open" and args[2] & _WRITE_FLAGS:
        _check_write(args[0])
    elif event == "os.rename":  # os.rename 和 os.replace 共用这个审计事件
        _check_write(args[0])
        _check_write(args[1])
    elif event in {"os.mkdir", "os.remove", "os.rmdir", "os.utime"}:
        _check_write(args[0])


def _guard_json_write(write):
    @wraps(write)
    def checked(path, *args, **kwargs):
        _check_write(path)
        return write(path, *args, **kwargs)
    return checked


sys.addaudithook(_audit)
os.environ["CMCC_DASHBOARD_DATA"] = str(_DATA)
os.environ["CMCC_NOTIFY_DRY"] = "1"

import common as _common

_common.GLOBAL_SETTINGS_FILE = _DATA / "settings.json"
_common.AWARDS_FILE = _DATA / "awards.json"
_common.NOTIFY_STATE_FILE = _DATA / "notify_state.json"
_common.POLICY_FILE = _DATA / "policy.json"
_common.atomic_write_text = _guard_json_write(_common.atomic_write_text)
_common.atomic_write_json = _guard_json_write(_common.atomic_write_json)
_common.update_json = _guard_json_write(_common.update_json)

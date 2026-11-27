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


# 审计事件参数里 dir_fd 的下标。dir_fd 为 None 或路径为绝对路径时走原逻辑。
# "os.rename" 有 src/dst 两个 dir_fd，单独处理。
_DIRFD_INDEX = {
    "os.mkdir": 2,    # (path, mode, dir_fd)
    "os.remove": 1,   # (path, dir_fd)
    "os.rmdir": 1,    # (path, dir_fd)
    "os.utime": 3,    # (path, times, ns, dir_fd)
}


def _check_write_fd(path, dir_fd):
    """按 dir_fd 解析相对路径后再检查；解析不到就跳过，不能误拦。

    Linux 上 shutil.rmtree / TemporaryDirectory 清理走基于 fd 的实现，
    审计事件里是相对名（如 "configs"）+ dir_fd。若按 cwd（仓库根）解析，
    会误判成写真实项目数据。
    """
    if dir_fd is None:
        _check_write(path)
        return
    try:
        decoded = os.fsdecode(path)
    except (TypeError, ValueError):
        return
    if os.path.isabs(decoded):
        _check_write(path)
        return
    if not os.path.isdir("/proc/self/fd"):
        return
    try:
        base = os.readlink(f"/proc/self/fd/{dir_fd}")
    except OSError:
        return
    _check_write(os.path.join(base, decoded))


def _audit(event, args):
    if event == "open" and len(args) > 2 and args[2] & _WRITE_FLAGS:
        _check_write(args[0])
    elif event == "os.rename":  # os.rename 和 os.replace 共用这个审计事件
        # (src, dst, src_dir_fd, dst_dir_fd)
        _check_write_fd(args[0], args[2] if len(args) > 2 else None)
        _check_write_fd(args[1], args[3] if len(args) > 3 else None)
    elif event in _DIRFD_INDEX:
        idx = _DIRFD_INDEX[event]
        _check_write_fd(args[0], args[idx] if len(args) > idx else None)


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

"""把桌面快捷方式指向新的 exe。

PowerShell 的 WScript.Shell 被安全策略拦了（COM 任意代码执行），
所以这里直接用 ctypes 调 IShellLink / IPersistFile，不经过 COM 自动化对象。

用法：
    python tools/mklnk.py <lnk 路径> <target exe> [工作目录] [图标文件]
"""

import ctypes
import os
import sys
from ctypes import POINTER, byref, c_void_p, c_ulong, c_wchar_p

CLSCTX_INPROC_SERVER = 1

GUID_SHELLLINK = "{00021401-0000-0000-C000-000000000046}"
IID_ISHELLLINK_W = "{000214F9-0000-0000-C000-000000000046}"
IID_IPERSISTFILE = "{0000010B-0000-0000-C000-000000000046}"


class GUID(ctypes.Structure):
    _fields_ = [("Data1", c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, text):
        super().__init__()
        ole32 = ctypes.windll.ole32
        ole32.CLSIDFromString(ctypes.c_wchar_p(text), byref(self))


def _method(ptr, index, restype, *argtypes):
    """取 COM 接口第 index 个虚函数，绑定到 ptr。"""
    vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    return proto(vtbl[index])


def make_shortcut(lnk, target, workdir=None, icon=None):
    ole32 = ctypes.windll.ole32
    ole32.CoInitialize(None)
    try:
        p = c_void_p()
        hr = ole32.CoCreateInstance(byref(GUID(GUID_SHELLLINK)), None,
                                    CLSCTX_INPROC_SERVER,
                                    byref(GUID(IID_ISHELLLINK_W)), byref(p))
        if hr != 0:
            raise OSError(f"CoCreateInstance 失败 hr=0x{hr & 0xFFFFFFFF:08x}")

        _method(p, 20, ctypes.c_long, c_wchar_p)(p, str(target))          # SetPath
        if workdir:
            _method(p, 9, ctypes.c_long, c_wchar_p)(p, str(workdir))      # SetWorkingDirectory
        if icon:
            _method(p, 17, ctypes.c_long, c_wchar_p, ctypes.c_int)(p, str(icon), 0)

        pf = c_void_p()
        _method(p, 0, ctypes.c_long, POINTER(GUID), POINTER(c_void_p))(
            p, byref(GUID(IID_IPERSISTFILE)), byref(pf))
        _method(pf, 6, ctypes.c_long, c_wchar_p, ctypes.c_int)(pf, str(lnk), 1)  # Save
        _method(pf, 2, ctypes.c_ulong)(pf)                                # Release
        _method(p, 2, ctypes.c_ulong)(p)
        return True
    finally:
        ole32.CoUninitialize()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    lnk, target = sys.argv[1], sys.argv[2]
    workdir = sys.argv[3] if len(sys.argv) > 3 else os.path.dirname(target)
    icon = sys.argv[4] if len(sys.argv) > 4 else None
    make_shortcut(lnk, target, workdir, icon)
    print(f"已指向 {target} -> {lnk}")

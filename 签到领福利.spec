# -*- mode: python ; coding: utf-8 -*-
# 唯一的打包配置：build_exe.bat 直接构建本 spec，产物即「签到领福利.exe」
# （界面按钮叫「签到领福利」，用户认这个名字）。改打包参数只改这一处。
from PyInstaller.utils.hooks import collect_all

datas = [('static', 'static')]
binaries = []
hiddenimports = []
tmp_ret = collect_all('webview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# 消消乐要用 AES 造请求头（Crypto.Cipher.AES）。不显式收进来，exe 里跑
# run_all --xxl 会报「缺少 pycryptodome」——源码跑正常、打包后就失效。
tmp_ret = collect_all('Crypto')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# 本机 site-packages 里装了 numpy / Pillow（OI 逆向脚本与截图工具留的），
# PyInstaller 会把它们连带打进 exe，体积从 20MB 涨到 40MB。项目本身和
# webview 都不 import 它们（已核 xref：无任何上游引用），直接排掉。
# 以后若真要用图像/数值库，先在这里删掉对应名字，否则 exe 里会「缺模块」。
excludes = ['numpy', 'PIL', 'scipy', 'pandas', 'matplotlib']


a = Analysis(
    ['app_desktop.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='签到领福利',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['app.ico'],
)

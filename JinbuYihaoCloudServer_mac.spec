# -*- mode: python ; coding: utf-8 -*-
"""进步壹号 云端服务 —— macOS 打包配置（v0.70）

服务端是命令行程序，不需要 .app bundle，出单文件可执行即可。
注意 upx=False：UPX 处理过的 Mach-O 在 macOS 上会被判为已损坏。
"""
import os

ROOT = os.path.abspath(SPECPATH)                       # noqa: F821

a = Analysis(                                          # noqa: F821
    [os.path.join(ROOT, 'cloud_server.py')],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)                                      # noqa: F821

exe = EXE(                                             # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='JinbuYihaoCloudServer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

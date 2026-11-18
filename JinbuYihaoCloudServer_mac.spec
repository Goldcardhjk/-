# -*- mode: python ; coding: utf-8 -*-
"""进步壹号 云端服务 —— macOS 打包配置（v0.70）

服务端是命令行程序，不需要 .app bundle，出单文件可执行即可。
注意 upx=False：UPX 处理过的 Mach-O 在 macOS 上会被判为已损坏。
"""
import os

ROOT = os.path.abspath(SPECPATH)                       # noqa: F821

# 与主程序一致：目标架构由 JINBU_TARGET_ARCH 指定，
# 否则 arm64 的 DMG 里会混进一个 Intel 架构的服务端可执行文件。
TARGET_ARCH = os.environ.get("JINBU_TARGET_ARCH") or None

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
    target_arch=TARGET_ARCH,
    codesign_identity=None,
    entitlements_file=None,
)

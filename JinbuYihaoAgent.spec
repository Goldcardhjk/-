# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    # v0.11：ComfyUI UI 资产（onefile 下随包解压到 _MEIPASS/images/ui，
    # images_util.UI_DIR 按 __file__ 定位，自动命中）
    datas=[(r'D:\进步壹号\进步壹号Agent\images\ui', 'images/ui')],
    hiddenimports=['segno'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy'],
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
    name='JinbuYihaoAgent_v069',
    debug=False,
    # v0.64：应用图标（星环徽章，多尺寸内嵌；任务栏/桌面快捷方式/安装包使用）
    icon=r'D:\进步壹号\进步壹号Agent\images\ui\app_icon.ico',
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=r'D:/进步壹号/进步壹号Agent/_tmp',
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

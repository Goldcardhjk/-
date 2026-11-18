# -*- mode: python ; coding: utf-8 -*-
"""进步壹号 Agent —— macOS 打包配置（v0.70）

与 Windows 版 JinbuYihaoAgent.spec 的差异，逐条说明原因：

1. 资源路径用相对路径（SPECPATH），不写死盘符 ——
   CI 的工作目录是 runner 上的 checkout 目录，Windows 绝对盘符在 mac 上不存在。
2. runtime_tmpdir=None ——
   Windows 版指向 D 盘 _tmp 是为了满足「不往 C 盘写临时文件」的约束；
   macOS 没有盘符概念，None 即交给系统用 $TMPDIR（~/Library/Caches 下），
   这是 mac 应用的标准做法，不会污染用户目录。
3. 产物是 .app bundle（BUNDLE 段），不是单文件可执行 ——
   mac 上双击运行、Dock 图标、Info.plist 元数据都依赖 bundle 结构。
4. onedir 模式（EXE 不收 binaries/datas，交给 COLLECT）——
   onefile 的 .app 每次启动都要解压整包到临时目录，首启要十几秒；
   而且 Gatekeeper 对 onefile 包的签名校验更容易出问题。
5. upx=False ——
   UPX 压缩过的 Mach-O 会被 macOS 代码签名和 Gatekeeper 判为已损坏。
6. 图标用 .icns，由 CI 里的 iconutil 从 app_icon_512.png 生成。
"""
import os

ROOT = os.path.abspath(SPECPATH)                       # noqa: F821
ICON = os.path.join(ROOT, "build_mac", "app_icon.icns")

a = Analysis(                                          # noqa: F821
    [os.path.join(ROOT, 'main.py')],
    pathex=[ROOT],
    binaries=[],
    # UI 资产随包；images_util.UI_DIR 按 __file__ 定位，
    # 在 .app 里会落到 Contents/Frameworks/images/ui，自动命中。
    datas=[(os.path.join(ROOT, 'images', 'ui'), 'images/ui')],
    hiddenimports=['segno'],
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
    [],
    exclude_binaries=True,                             # onedir：二进制交给 COLLECT
    name='JinbuYihaoAgent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                                         # 见文件头第 5 条
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=True,                               # 支持 Finder 拖拽文件打开
    target_arch=None,                                  # 跟随 runner 架构
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON if os.path.exists(ICON) else None,
)

coll = COLLECT(                                        # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='JinbuYihaoAgent',
)

app = BUNDLE(                                          # noqa: F821
    coll,
    name='进步壹号.app',
    icon=ICON if os.path.exists(ICON) else None,
    bundle_identifier='cn.jbyh.agent',
    version='0.70',
    info_plist={
        'CFBundleName': '进步壹号',
        'CFBundleDisplayName': '进步壹号 Agent',
        'CFBundleShortVersionString': '0.70',
        'CFBundleVersion': '0.70',
        'NSHumanReadableCopyright': '进步壹号 Agent · 关系经营工作台',
        # Retina 下不声明这项，Tk 窗口会被系统按 1x 位图拉伸，整个界面发虚
        'NSHighResolutionCapable': True,
        'NSRequiresAquaSystemAppearance': False,
        'LSMinimumSystemVersion': '11.0',
        'LSApplicationCategoryType': 'public.app-category.productivity',
    },
)

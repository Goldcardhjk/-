# 进步壹号 Agent · macOS 版构建与使用说明

> 本文件说明如何用 GitHub Actions 在云端产出 macOS 安装包（本地不需要 Mac），
> 以及拿到 DMG 之后如何安装运行。

---

## 一、为什么要用 CI 出包

PyInstaller **不支持交叉编译**。`.app` 必须在 macOS 上构建——冻结产物要链接目标平台的
原生库（macOS 的 Tcl/Tk framework、Pillow 的 `.dylib`、Mach-O 格式的 Python 解释器），
这些在 Windows 机器上并不存在。在 Windows 上强行打包只会得到一个无法启动的壳。

GitHub Actions 提供免费的 macOS runner，可以在不买 Mac 的前提下产出安装包。

---

## 二、首次配置（约 5 分钟，只需做一次）

### 1. 把项目推到 GitHub 仓库

当前工作区还不是 git 仓库，需要先初始化。**注意不要把密钥和构建产物传上去**，
仓库里已有的 `.gitignore` 覆盖了 `_keys/`、`pay_config.json`、`dist_v068/`、`dist_v069/`。

```bash
cd D:/进步壹号/进步壹号Agent
git init
git add .
git commit -m "v0.70: macOS 支持"
git remote add origin https://github.com/<你的账号>/<仓库名>.git
git push -u origin main
```

> 建议把仓库设为 **Private**。免费账号的私有仓库同样可以用 macOS runner，
> 只是有每月分钟数配额（macOS runner 按 10 倍系数计费，一次构建约消耗 50–100 分钟配额）。

### 2. 确认 Actions 已启用

仓库页面 → Settings → Actions → General → 选 "Allow all actions"。

---

## 三、出包（每次发版执行）

### 方式 A：手动触发

仓库页面 → **Actions** 标签 → 左侧选 **Build macOS App** → 右侧 **Run workflow**
→ 填版本号（默认 0.70）→ 点绿色按钮。

约 8–15 分钟后，在该次运行的页面底部 **Artifacts** 区下载：

- `进步壹号_v0.70_macOS_arm64.dmg` —— Apple Silicon（M1/M2/M3/M4）
- `进步壹号_v0.70_macOS_x86_64.dmg` —— Intel Mac

### 方式 B：打 tag 自动出包并发布 Release

```bash
git tag v0.70
git push origin v0.70
```

两个 DMG 会自动挂到 GitHub Release 页面，可直接把链接发给用户下载。

---

## 四、构建流程做了什么

| 步骤 | 说明 |
|---|---|
| 安装 Python 3.12 | 用 python.org 官方构建，**自带 Tk 8.6** |
| 校验 Tk 版本 | 低于 8.6 直接让构建失败，避免产出界面是坏的包 |
| 生成 `.icns` | 用 `sips` + `iconutil` 从 `app_icon_512.png` 生成多尺寸图标 |
| 打包 `.app` | `JinbuYihaoAgent_mac.spec`，onedir 模式 + BUNDLE |
| 打包云端服务 | `JinbuYihaoCloudServer_mac.spec`，命令行可执行 |
| 结构冒烟 | 校验 Info.plist、可执行位、UI 资源是否随包、架构 |
| 导入冒烟 | 确认全部业务模块在 macOS 上 import 通过，且命中了中文字体 |
| ad-hoc 签名 | 让 `.app` 能在本机启动（不需要开发者账号） |
| 制作 DMG | 带 Applications 软链的拖拽安装布局 |

---

## 五、安装与首次运行

1. 双击 DMG，把「进步壹号」拖到 Applications 文件夹。
2. **首次打开必须右键点图标 →「打开」→ 在弹窗里再点「打开」。**

> ### 为什么要右键打开？
>
> CI 产出的包只做了 **ad-hoc 签名**，没有经过 Apple 公证（notarization）。
> 直接双击会被 Gatekeeper 拦下，提示「无法打开，因为无法验证开发者」或
> 「已损坏，应移至废纸篓」。右键「打开」只需做一次，之后就能正常双击。
>
> 如果提示「已损坏」且右键也无效，在终端执行：
> ```bash
> xattr -cr /Applications/进步壹号.app
> ```
> 这是清掉下载文件的隔离属性，不是真的损坏。
>
> 要做到用户双击直接打开，需要 Apple 开发者账号（99 美元/年），
> 在工作流里补 `codesign` 与 `notarytool` 两步。

---

## 六、macOS 版与 Windows 版的差异

| 项 | Windows | macOS |
|---|---|---|
| 产物 | 单文件 `.exe` | `.app` bundle（onedir） |
| 界面字体 | 微软雅黑 | **苹方 PingFang SC**（自动识别） |
| 数据目录 | exe 同级 `data/` | `.app` 同级 `data/`，或用 `JINBU_DATA_DIR` 指定 |
| 临时目录 | 固定 D 盘 `_tmp` | 系统 `$TMPDIR` |
| 压缩 | UPX 开启 | **UPX 关闭**（会破坏 Mach-O 签名） |

### 数据目录的注意事项

`.app` 被拖进 `/Applications` 后，同级目录是系统保护区，**不可写**。
首次启动若报权限错误，用环境变量指定一个可写位置：

```bash
# 推荐：放用户目录下
mkdir -p ~/Library/Application\ Support/JinbuYihao
JINBU_DATA_DIR=~/Library/Application\ Support/JinbuYihao \
  open -a 进步壹号
```

或者不拖进 Applications，直接放在 `~/Documents/进步壹号/` 下运行。

---

## 七、代码侧为 macOS 做的适配（v0.70）

1. **UI 字体跨平台**（`ui_base.py`）
   新增 `UI_FONT` 常量与 `_resolve_ui_font()`：按平台从候选表里挑一个系统里真实
   存在的中文字体。macOS 优先苹方，Windows 仍是微软雅黑，行为不变。
   原先全工程 17 处硬编码 `"Microsoft YaHei"` 已全部替换。

2. **PIL 字体路径跨平台**（`images_util.py`）
   原本只有 `C:/Windows/Fonts/` 三条候选，在 macOS 上全部 miss 后会退到
   `ImageFont.load_default()`——那是个**位图字体，画不出中文**，人物卡和关系标签
   会变成一排方块。现按平台返回候选表（mac 走 `/System/Library/Fonts/PingFang.ttc`）。

3. **MCP 配置示例去盘符**（`mcp_server.py`）
   文档字符串里的 `D:/进步壹号/...` 改为占位符，并补了 macOS 示例与 shell 写法。

> 业务代码本身无 Windows API 依赖：`ctypes.windll` 只出现在录制与验收工具里，
> 不参与打包。数据目录定位已按 `sys.frozen` 取可执行文件所在目录，逻辑通用。

---

## 八、已知限制

- CI runner 没有图形登录会话，**无法在云端真实启动窗口做视觉验收**。工作流做的是
  结构冒烟 + 导入冒烟；界面实际效果需要在真机上首次运行时确认。
- 未做 Apple 公证，分发给他人时需引导对方右键打开。
- Intel 版（x86_64）在 `macos-13` runner 上构建。GitHub 已宣布逐步下线 macOS 13，
  届时可改用 `macos-14` + `--target-arch universal2` 出通用包。

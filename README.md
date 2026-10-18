# WallpaperRotate 壁纸轮换器

Windows 绿色单文件壁纸轮换工具：按权重随机更换壁纸（新图权重高、老图保底），
由 Windows 计划任务定时执行，设置界面直接用系统默认浏览器打开，无需安装任何运行时。

**多屏同图轮换**：多显示器环境下，每块屏幕都显示同一张壁纸（各自按自身分辨率填充），
可整组替代 Windows 自带的「幻灯片放映」桌面背景任务。

## 功能

- **多屏同图轮换（替代 Windows 幻灯片）**：多显示器时每块屏铺同一张图、同步轮换；
  相比 Windows 自带幻灯片，支持加权随机、按分辨率/大小筛选、静默后台运行
- **加权随机轮换**：半衰期模型——越新的图权重越高，老图有保底权重，换壁纸不重复也不腻
- **定时自动执行**：注册 Windows 计划任务（默认每 6 小时），静默运行无窗口
- **浏览器设置界面**：双击 EXE 即在系统默认浏览器打开本地设置页（127.0.0.1），保存/轮换/任务管理都在页面上完成
- **分辨率档位快调**：最小宽度旁的 ▲▼ 箭头按 1080P → 2K → 4K → 16K 整档切换，高度、文件大小同步；也支持键盘手输任意值，手改后按箭头自动跳到最近档位
- **跨机器自适应**：从别的电脑拷贝配置后，`C:\Users\<旧用户名>\Pictures` 之类的路径自动适配为本机当前用户的目录，config.json 自动修正
- **零依赖单文件**：纯 Python 标准库，PyInstaller onefile 打包，不依赖 .NET/VC++ 运行库
- 多文件夹配置（分号分隔）、轮换日志、窗口状态记忆

## 使用

1. 从 [Releases](../../releases) 下载 `WallpaperRotate.exe`（或自行构建，见下）
2. 双击运行 → 自动打开设置页
3. 选择图片文件夹（留空则用本机“图片”文件夹）→ 点「安装计划任务」
4. 之后每 6 小时自动换一次；想手动换就双击 EXE 点「立即换一张」

设置页空闲 15 分钟自动退出，不影响计划任务后台运行。

## 从源码构建

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name WallpaperRotate \
  --icon "C:\WallpaperRotate\icon.ico" \   # 注意：--icon 必须用绝对路径
  wallpaper_gui.py
```

要求：Windows 10/11 x64，Python 3.10+（仅构建需要，运行不需要）。

## 命令行参数

| 参数 | 作用 |
|---|---|
| （无参数） | 打开设置界面 |
| `--run` | 静默轮换一次（计划任务实际调用的就是它） |
| `--install` / `--uninstall` | 安装 / 卸载计划任务 |

## 配置说明

实际配置为 EXE 同目录的 `config.json`（已 gitignore，含本机路径），字段见
[config.example.json](config.example.json)：

| 字段 | 含义 |
|---|---|
| `folders` | 图片文件夹列表，留空 = 本机“图片”文件夹 |
| `interval_hours` | 轮换间隔（小时） |
| `half_life_days` | 新图权重半衰期，越小越偏新图 |
| `min_weight` | 老图保底权重（0–1） |
| `min_size_mb` | 参与轮换的最小文件大小 |
| `min_width` / `min_height` | 参与轮换的最小分辨率 |

## 实现要点

- 换壁纸：`SystemParametersInfoW(SPI_SETDESKWALLPAPER)` + 注册表 `WallpaperStyle=10`（填充）。
  该填充样式会让 Windows 在**每一块显示器上各自按分辨率铺同一张图**，因此天然实现多屏同图轮换；
  若想改成「一张图跨屏拉伸成全景」，把样式改成 `22`（Span）即可
- 图片尺寸：直接解析 PNG/JPEG 文件头，不加载整图，扫描快
- 计划任务：`schtasks.exe`，失败回退 PowerShell `Register-ScheduledTask`（仅以当前用户身份运行，不需要管理员权限）
- 设置界面：`http.server.ThreadingHTTPServer` 监听 127.0.0.1:18106（占用则回退随机端口），纯标准库

## 目录结构

```
wallpaper_gui.py    主程序（单文件，全部逻辑）
icon.ico            应用图标
config.example.json 配置模板
legacy/             最初的 PowerShell 版本（已被 Python 版取代，仅存档）
```

## 许可

MIT

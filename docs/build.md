# 怎么打的包

## 环境

- Windows 10/11 x64，Python 3.13
- `pip install pywebview==6.2.1 pyinstaller==6.22.3`

## 一条命令

```bash
python -m PyInstaller --noconfirm --clean desktop-agent.spec
```

产物在 `dist/DesktopAgent/`，双击 `DesktopAgent.exe` 就出窗口：

- 主程序 `DesktopAgent.exe` 约 8.5 MB，整个目录约 100 MB（onedir）
- `console=False`，不会弹黑框
- 首次启动会在 `~/DesktopAgentWorkspace/` 建工作区，并把 `samples/`、`AGENTS.md` 复制进去

## 三个坑（都是踩过的）

1. **uvicorn 的隐藏导入**：`uvicorn.logging`、`uvicorn.loops.auto`、
   `uvicorn.protocols.http.auto`、`uvicorn.protocols.websockets.auto`、
   `uvicorn.lifespan.on` 全部要写进 `hiddenimports`，否则打包后启动即退出。
2. **资源路径**：`ui/`、`skills/`、`scripts/` 打包后在 `sys._MEIPASS`；
   可写目录（Key、checkpoint、通知日志）必须用 `Path.home()`。
   见 `desktop.py: bundle_root()` 与 `prepare_workspace()`。
3. **Key 存哪**：打包后 cwd 可能是安装目录（不可写），
   所以 `/config` 把 Key 写到 `~/.desktop-agent/.env`，`agent/config.py` 会读它。

## 验证打包结果

```powershell
$p = Start-Process dist\DesktopAgent\DesktopAgent.exe -PassThru
Start-Sleep 12
if (-not $p.HasExited) { "窗口起来了" } else { "启动失败，退出码 $($p.ExitCode)" }
Stop-Process -Id $p.Id -Force
```

进程存活 + 有监听端口，说明本地服务与窗口都正常。

## 发 Release

```bash
Compress-Archive -Path dist\DesktopAgent\* -DestinationPath dist\DesktopAgent-windows-x64.zip -Force
```

在 GitHub 仓库 → Releases → Draft a new release：
- Tag：`v1.0.0`（对应大作业基线）
- 附件：`DesktopAgent-windows-x64.zip`
- 说明里写：解压后双击 `DesktopAgent.exe`，首次启动在界面左下角「配置 Key」填 `OPENAI_API_KEY`

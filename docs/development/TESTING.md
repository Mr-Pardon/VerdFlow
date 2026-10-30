# 开发与测试

## 环境

- Python 3.11 或更新版本；Windows 便携构建使用 CPython 3.13 x64。
- Node.js 22.12 或更新的受支持版本，以及 package.json 指定的 pnpm。
- Blender 和 DTS Explorer 是可选集成环境，不随源码提供。

在项目根目录创建隔离 Python 环境。以下 PowerShell 命令安装单元测试使用的依赖，
并让 Python 从当前源码读取 workspace 包：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install "jsonschema>=4.22,<5" "PyYAML>=6,<7" "pyshp>=2.3" "rasterio>=1.4" "shapely>=2"
$env:PYTHONPATH = ((Get-ChildItem -Path packages,apps,adapters -Directory |
    ForEach-Object { Join-Path $_.FullName 'src' } |
    Where-Object { Test-Path -LiteralPath $_ }) -join [IO.Path]::PathSeparator)
corepack pnpm install --frozen-lockfile
node apps/studio-electron-probe/node_modules/electron/install.js
```

Windows 虚拟环境的 Python 可能是转发启动器，导致依赖原生进程身份的 Worker 测试观察到
父子 PID 不同。完整测试应使用实际 CPython 可执行文件；以下方式仍从虚拟环境加载依赖，
不放宽 PID 校验，也不更换正式 Worker 协议：

```powershell
$basePython = python -c "import sys; print(getattr(sys, '_base_executable', sys.executable))"
$env:PYTHONPATH += [IO.Path]::PathSeparator + (Join-Path $PWD '.venv/Lib/site-packages')
& $basePython -S -m unittest discover -s tests/unit
```

此处 `Lib/site-packages` 是 Windows 虚拟环境布局；其他平台按其环境布局配置。

Electron 的官方安装脚本会检查并下载当前锁定版本的原生运行文件。首次安装或复用包管理器
缓存时仍执行此步，不能仅凭 `pnpm install` 成功就认为 `electron.exe` 已就绪。

`tools/portable-requirements.txt` 是 Windows CPython 3.13 的固定 wheel 清单，供便携构建
使用；其中平台相关哈希不能直接用于任意操作系统。Python 包版本与产品版本独立管理。

## 自动化验证

GitHub CI 在 Windows x64 上使用 CPython 3.13.3、Node.js 24 和工作区指定的 pnpm，
从固定哈希的 wheel 清单安装依赖，执行 Python、两端 UI、桌面壳和品牌测试及生产构建。
CI 不调用真实 DTS/Blender，不使用维护者密钥，也不自动发布 Release。

```powershell
# 非转发式 Python 可直接执行；Windows 虚拟环境按上段命令运行。
python -m unittest discover -s tests/unit
corepack pnpm --filter @verdflow/studio test
corepack pnpm --filter @verdflow/dts-live-webui test
corepack pnpm --filter @verdflow/studio-electron-probe check
corepack pnpm typecheck
corepack pnpm build
corepack pnpm build:dts-live-webui
```

单元测试生成自己的临时数据，不要求下载客户模型或使用现有用户 Library。
缺少可选运行环境时的跳过不等于集成通过。真实 Blender/DTS 集成须另外验证初始化、
实际命令回执、坐标、取消与进程清理，不能以模拟测试替代。

## 本地开发

仓库镜像维护见 [Gitee Mirror](GITEE_MIRROR.md)。它有独立的 `.github/tests` 回归，不进入应用运行链路。

```powershell
corepack pnpm dev:desktop
```

编排器创建本次 Local API 与 Vite 进程，开发数据默认位于 `_work/studio-dev/`。
`VERDFLOW_PYTHON` 可指定 Python 可执行文件；`VERDFLOW_DEV_LIBRARY_PATH` 可指定开发库。
不要将开发库指向唯一一份正式用户数据。默认开发库遇到不兼容格式时可能重建；显式指定
的库不会自动删除。关闭桌面窗口后检查本轮子进程已退出。

## 测试数据与结果

`asset-packs/` 中的索引和搭配用于契约与回归测试，不包含可再分发模型，也不是产品资源库。
`strategy-packs/` 保存生成器源码及兼容测试所需版本；安装包由显式的包导出流程生成。
探针结果、性能日志和用户提供的测试输入只保存在被忽略的本机目录，不作为测试前置条件。

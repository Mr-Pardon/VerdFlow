# @verdflow/studio-electron-probe

状态：Experimental
架构分类：App / Desktop Shell
维护责任：Desktop shell maintainers

## 职责

提供 Studio 的 Electron PlatformBridge：窗口、受限 IPC、文件对话框、资源定位、
loopback Local API 代理与 Python sidecar 生命周期。包名保留 probe 以维持现有工具入口，
不允许 Renderer 因此导入 Electron 或获得 Node.js 权限。

## 公共入口

- `src/main.mjs`：主进程与能力处理器。
- `src/preload.cjs`：通过 context isolation 暴露最小 Bridge。
- `src/channels.mjs`：受控 IPC 通道。
- `src/portable-runtime.mjs`：用户数据、Renderer 与 Python 运行路径契约。
- `src/portable-updater.mjs`：更新状态机；`update-source.mjs` 限制来源与任务门禁，
  `update-runner.mjs` 管理准备和替换助手。见 [ADR-0015](../../docs/adr/0015-portable-application-updates.md)。
- `src/manual-link.mjs`：仅允许固定用户手册首页在系统浏览器打开。

## 依赖边界

不解释项目、策略或 XML 语义，不读取项目数据库。管理令牌留在主进程，
只代理规定的 `/api/v1/` loopback 请求，不允许任意导航、URL 或请求头。

## 验证

```powershell
corepack pnpm --filter @verdflow/studio-electron-probe check
corepack pnpm typecheck
corepack pnpm build
corepack pnpm dev:desktop
```

开发启动与 Python 配置见 [开发与测试](../../docs/development/TESTING.md)。
Windows 更新回归经过实际启动器，验证隐藏助手接管、父进程退出后的文件替换与启动、
无接管确认时拒绝替换，以及失败诊断和旧程序恢复。接管确认不改变用户数据。
Windows x64 便携构建输出在 `dist/portable/`。更新源默认来自桌面壳的 `updates.json`，
指向 `Mr-Pardon/VerdFlow`：

```powershell
python tools/build_portable_release.py --smoke-test
```

可用 `--update-repository owner/repository` 覆盖，或用 `--no-updates` 关闭联网更新。
暂无公开 Release 时保持正常状态；更新附件缺失仍报错。该构建获取固定版本和哈希的 Electron、Python 与 wheel，
生成 ZIP、SHA-256、逐文件清单及 `VerdFlow-update-win-x64.json`。
`tools/portable_licenses.py` 同时收集项目许可、运行依赖原文及版本索引，缺失声明时失败；
原生依赖的额外义务仍需按最终制品核对。应用图标来自 `assets/branding/`。

## 当前限制

当前提供免安装 ZIP，支持检查/下载更新和确认重启，不含安装器或代码签名。
0.4.0/1.0.0 的更新器启动故障需要手动下载 1.0.1 过渡一次。
发布仓库无正式附件时不能完成线上升级验收。窗口及本机对话框需要在交互式
桌面验证。产品用户数据使用稳定的 `%LOCALAPPDATA%/VerdFlow`，API Key 由系统凭据库保存；
开发启动默认隔离在 `_work/studio-dev/`，不从安装目录下的 UserData 读取正式数据。

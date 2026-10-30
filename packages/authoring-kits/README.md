# VerdFlow Authoring Kits

ADR-0020 的方案依赖补齐属于 SystemHost 与资源中心。VFS/VFA 作者无需添加下载代码、
资源中心 URL、兼容版本表或新的包内字段；资源中心从现有交换包静态检查结果派生发现信息。
方案来源资产库只是可选推荐，导入它不会自动更新方案的植被快照，也不代表安装外部 PAK。

ADR-0016：VFS Author 使用策略 SemVer，不要求维护兼容版本清单。导出工具包携带
Contracts 的同源版本解析模块；`build_vfs.py --previous <source-directory>` 可离线检查
破坏性变更（需要 PyYAML），不执行生成器。依赖方向为 Authoring Kits -> Contracts。

状态：Experimental  
架构分类：Infrastructure  
维护责任：VerdFlow Core Team

## 职责

该包只负责保存并确定性导出 VerdFlow 的 Agent 创作工具包，不参与种植执行、
Library 持久化或 Studio 展示计算。

当前包含：

- `vfs-author`：帮助 Agent 设计、实现、自检并打包 VerdFlow 策略。
- VFS Author 的模板与参考同时覆盖可选 `extensions.role_presentation`，展示资料随策略包
  分发，不要求修改应用代码；导出工具包本身不解析或渲染这些资料。
- `vfa-author`：帮助 Agent 将 DTS 植被资料整理并打包为 VerdFlow Catalog。

工具包使用普通 `.zip`，解压后根目录是同名 skill。`.zip` 工具包不是可直接导入
VerdFlow 的 `.vfs` 或 `.vfa` 业务数据包。

## 公共入口

- `available_authoring_kits()`
- `write_authoring_kit()`
- `AuthoringKitArchiveInfo`

## 依赖边界

只依赖 Contracts、Python 标准库和随 wheel 分发的只读资源。它不依赖 Core、LibraryStore、
Strategy Worker、Asset Package、SystemHost、Studio 或 Agent SDK。SystemHost 只调用
确定性导出入口，不把机器状态写回该包。

## 验证

运行 `python -m unittest tests.unit.test_authoring_kits`。测试同时使用正式 Strategy 和
Asset Package Inspector 检查模板附带构建器的输出。

## 当前限制

VFA Author 首版只覆盖 DTS `dts.static-mesh`；Blender 作者规范、发布者签名、自动 Agent
会话和产品内策略训练不属于本包。模板需要随业务 schema 的正式变更同步更新。

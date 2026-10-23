# verdflow-library-store

状态：Experimental  
架构分类：Infrastructure  
维护责任：Core infrastructure maintainers

## 职责

负责当前 OS 用户可跨项目复用的 VerdFlow Library。首版保存已安装 Strategy
Package 的受管副本、精确 package hash、安装元数据，以及用户对精确 hash 的信任和
撤销事件。当前 schema 同时保存按输出目标隔离的 Catalog、单一可编辑且自包含的
RoleBindingProfile，以及内容寻址的受管缩略图资源和 Catalog 引用关系。Profile 只用内部
generation 做乐观并发，不存在本地版本树或 LocalBinding 双轨。SQLite 使用 WAL 与短写事务；初始化
和 migration 由短生命周期内核锁串行。

该包不属于任何 ProjectStore，也不持有项目、Job、种植结果或运行 Snapshot。

按 ADR-0005，删除 Catalog 的非阻塞影响计数同时检查 Profile 的逐项来源链接，
不把多库方案的单库提示为空误判为没有引用；仍不级联修改或删除任何方案。

## 公共入口

其他 package 只从 `verdflow.library_store` 导入 `LibraryStore` 和公开错误类型。
`LibraryStore.open()` 在首次使用时创建用户库。产品首次发布前只接受当前 schema，旧测试库
必须重建，不提供 Profile 兼容 migration。它结构化实现
Core 的 `UserLibraryPort` 与 `StrategyTrustPort`，但不依赖 Core。

策略安装入口 `install_verified_strategy()` 只接受已经由 Strategy Worker
`inspect_strategy_package()` 校验过的元数据。LibraryStore 会复制并指纹校验包字节；
策略执行前仍必须由 Worker 再次校验语义 package hash。
策略卸载在单个数据库事务中删除精确 package hash 的注册和信任事件，再尽力清理已经
失去引用的托管目录；它不扫描或限制历史项目引用。

Catalog/Profile 写入使用 Planting 准备的 `AssetCatalogDocument` 和
`RoleBindingProfileDocument`；LibraryStore 验证 canonical bytes 与
semantic hash。Profile 保存完整目标引用、显示快照、权重和缩放，Catalog 只作为可选来源提示。
ADR-0016：Profile 显式保存允许同 major 向上更新版本，保存旧基线时只需存在适用策略；
角色/参数校验和信任解析仍由 SystemHost/Planting 负责。本包不检查生成器语义，
不在读取时升级 Profile，不改变乐观并发约束。

## 依赖边界

- 仅依赖 `verdflow-contracts` 和 Python 标准库。
- 不依赖 Core、ProjectStore、Planting、Strategy Worker、Blender、MCP 或 Studio。
- 不向调用方暴露 SQLite connection、row 或可写内部目录。
- Project Core Host 只通过只读端口查询；Library 写入由 CLI/SystemHost 组合根负责。

## 内部结构

```text
layout.py       user library directory layout
locking.py      initialization and migration serialization
database.py     WAL connection and short write transactions
schema.py       current schema bootstrap and compatibility reads
migrations.py   current-only pre-release schema gate
packages.py     immutable managed package copies and payload fingerprints
strategies.py   strategy installation metadata repository
trust.py        append-only trust decisions and current-state reads
catalogs.py     one mutable current Catalog per target/catalog identity
profiles.py     mutable self-contained Profiles and optimistic generations
resources.py    content-addressed managed thumbnail resources
identities.py   deterministic internal record identifiers
store.py        public facade and composition
```

## 验证

在仓库根目录运行：

```powershell
python -m unittest tests.unit.test_library_store -v
python -m unittest tests.unit.test_library_catalog_profiles -v
python -m unittest discover -s tests\unit -v
```

## 当前限制

- 用户显示顺序和 Profile candidate cache 尚未进入正式 schema。
- Catalog 只保存当前状态；`generation` 仅用于乐观并发，不是用户版本，也不形成版本树。
- `.vfa` 导出当前 Catalog 的不可变内容快照，但不会在本地创建或激活一个版本。
- Catalog 删除或变化不级联删除 Profile；Profile 的完整执行事实仍可独立使用。
- 包安装契约要求调用方先完成 Strategy Worker 检查；LibraryStore 负责受管复制和字节
  防篡改检查，Worker 在执行前负责语义 hash 的最终复核。
- 信任是用户主动接受风险，不是恶意代码沙盒或发布者签名。
- 写事务依赖 SQLite 的同机序列化；远程共享文件系统和多机写入不受支持。
- 已安装策略包支持按精确 package hash 卸载；注册与信任事实先在同一事务中删除，
  受管目录随后尽力清理。异常中断遗留的未注册目录尚无自动回收任务。
- Catalog/Profile canonical JSON 当前存储在 SQLite BLOB；大规模资产库的分块与外置
  对象阈值需在真实容量数据出现后再评估，不提前引入第二套对象库。
- Catalog 缩略图只允许引用 `verdflow-resource:res_<sha256>`。ResourceStore 当前接受
  PNG、JPEG、WebP 的上层导入检查，单资源限制 16 MiB；未引用资源的垃圾回收和资产包
  资源导出将在真实管理需求出现后设计。

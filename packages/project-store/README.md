# verdflow-project-store

状态：POC  
架构分类：Infrastructure  
维护责任：Core infrastructure maintainers

## 职责

负责 VerdFlow 活跃项目的持久化基础设施：SQLite 元数据与事务、单写入者锁、
内容寻址对象存储、Artifact 去重与回收、当前 schema 门禁，以及同主机
失效写入者的保守恢复与审计。Schema v23 同时保存叶子/Assembly Source、
Assembly member、CoordinateFrame、
SceneRevision、Job 当前快照、不可覆盖的 Attempt、可补读 JobEvent 和外部
WorkerProcess provenance，以及由成功 Job 定版的 RegionSet 与可查询 Region
摘要、PlantingRun、父子 PointSet、策略包快照、稀疏 Catalog 使用快照、有效绑定
快照、目标资产分配摘要、不可变 ExportRecord、轻量 Plant Run execution、Material Flow
execution、Scene Preparation execution read model，以及显式指向唯一当前产品链的
`ProjectWorkflowState`。固定场景的连续种植使用一份当前 `PlantingComposition`：每个
`PlantingBatch` 只引用既有 PointSet、AssignmentSet、Plant Run 和精确 Region ID；组合
Export 记录保存有序多输入谱系，不复制点位或拼接输出文件。
成功组合 Export 不再从属于当前 Composition 生命周期；增删或替换批次不会级联删除 XML
记录。`composition_output_executions` 保存最终输出的轻量推进状态，文件只在用户显式删除后
解除项目引用。
提交批次时可指定一个当前批次作为替换目标；Repository 在同一事务内校验 Region 集合、
删除旧引用、写入新引用并保留 ordinal，失败时旧批次保持不变。
Job 记录还保存 Core 冻结的 retention class、期限和终态清理时间；本包按引用图
原子删除过期逻辑记录。Core 可以将逻辑 sweep 与物理 object GC 分开调度；直接
调用完整 sweep 时仍可在事务提交后立即回收对象。
Job `progress` 表示当前 Attempt 的进度：同一 Attempt 内只允许单调增加；过期 Attempt
先保留其中断事件，再在重排为 queued 时归零，让下一 Attempt 从真实阶段重新报告。

该包不解释绿地分类、种植策略、坐标转换或任何平台输出格式。

按 ADR-0005，资产使用快照允许 `draft` 来源，ID 为自包含搭配语义 hash，不创建库对象。
v19-v22 项目打开时先备份并迁移，保留原快照 ID、Artifact 和外键关系。

## 公共入口

其他 package 只从 `verdflow.project_store` 导入 `ProjectStore`、
`ProjectQueryStore`、`ProjectStorageProfile`、`RecoveryPolicy` 和公开错误类型。
`ProjectStore.create()/open()` 默认采用 `ProjectStorageProfile.DURABLE`，使用 SQLite
`WAL + synchronous=FULL` 保证普通项目的崩溃恢复语义。只有明确不承诺跨进程恢复、并在
会话结束后整体删除的临时运行时，才允许显式选择 `EPHEMERAL`；该模式使用内存 journal、
关闭同步刷盘并把 SQLite 临时页放入内存，但数据库、对象和最终输出仍位于临时项目目录。
存储 Profile 只决定物理耐久级别，不改变领域状态机、事务边界或输出格式。

`ProjectStore.open()` 默认采用 `SAFE`
恢复策略；上层可显式传入 `RecoveryPolicy.REFUSE` 禁止自动恢复。返回数据使用
`verdflow.contracts` 中的契约。

`ProjectQueryStore.open()` 是不持有 writer lock 的只读投影，用于查询已提交的
Project、游标化 Job 列表、JobEvent、JobAttempt、SceneRevision、PlantingRun、顶层
execution 和 Artifact 元数据。它不执行
migration 或恢复，并应由上层专用查询线程持有，不能作为第二写入者。每次公开查询使用
短只读事务固定一个 WAL snapshot；`open_artifact()` 只按已提交 Artifact 的 content hash
打开不可变对象流，供 SystemHost 形成有类型产品投影，不向产品界面暴露对象路径或原始
存储布局。`read_artifact_chunk()` 为 Local API 下载流提供有界读取，不负责外部文件保存。
`get_latest_export_record()` 按 PointSet 与 AssetAssignmentSet 查询最近一次不可变
ExportRecord，供产品层在页面重建或项目重开后恢复最后采用的输出参数；它不建立或更新
“当前导出”指针。
`create_export_record(require_terrain_hit=True)` 按已验证的 hit_count 摘要校验数量；
同时筛选资产时才读取规范点集和分配 Artifact 重算交集。默认仍按完整
资产选择计数；过滤规则由 producer Job 的命令 Artifact 保存，不新增可变输出版本。

上层不得导入内部 repository、数据库连接、锁实现或对象路径。
Adapter 需要读取输入时调用 `materialize_artifact()` 将内容复制到自己的私有
运行目录；该 API 不暴露对象库内部路径。

## 依赖边界

- 依赖 `verdflow-contracts` 和 Python 标准库。
- 不依赖 Core、Geometry、Planting、Adapter、MCP、Blender 或 Studio。
- 不向上层暴露 SQLite row、内部路径或无结构存储记录。

## 内部结构

```text
layout.py        project directory layout
locking.py       writer ownership
processes.py     OS process identity inspection
database.py      connection / transaction / checkpoint
storage.py       explicit durable / ephemeral SQLite policy
query_store.py   bounded read-only projection for concurrent monitoring
schema.py        current schema bootstrap and validation
migrations.py    current-only pre-release schema gate
integrity.py     database and referenced-object validation
recovery.py      stale-lock decision, recovery and audit
object_store.py  immutable blob lifecycle
artifacts.py     artifact metadata repository
job_records.py   SQLite row mapping and persistence guards
job_events.py    monotonic event append and replay
job_recovery.py  expired attempt convergence
jobs.py          submission and active lifecycle transactions
retention.py     expired Job / orphan Snapshot / Artifact reference-aware cleanup
projects.py      project metadata repository
scenes.py        leaf/assembly source / frame / scene revision repository
regions.py       immutable region set and queryable region summaries
points.py        planting run / immutable point set lineage and summaries
snapshots.py     immutable package / catalog / binding / assignment references
exports.py       immutable provider output provenance and Artifact reference
plant_run_executions.py persistent composed-run, Review continuation, and optimistic revision guard
material_flow_executions.py persistent material-analysis flow and confirmation state
scene_preparation_executions.py persistent Source-to-Region product workflow state
workflow.py      single current product-chain pointers and optimistic revision guard
planting_compositions.py single current composition, batch references, and Region occupancy
composition_exports.py immutable multi-batch output records and ordered input lineage
worker_processes.py external process provenance repository
store.py         public facade
```

## 验证

在仓库根目录运行：

```powershell
python -m unittest discover -s tests\unit -v
python -m unittest tests.unit.test_project_query_store -v
python -B tools\project_store_probe.py --help
python -B tools\project_store_recovery_probe.py
```

真实文件探针接受调用方提供的输入，结果按[集成证据约定](../../tests/integration/results/README.md)
保存在本地；不依赖某一份历史报告作为运行前置条件。

## 当前限制

- 只支持单写入者。
- `EPHEMERAL` 不承诺进程崩溃后的事务恢复，只能由负责整体清理临时目录的组合根选择；
  普通项目和外部调用默认保持 `DURABLE`。
- 自动恢复只适用于同主机、进程已死亡或 PID 已复用且身份可确认的 v1 锁记录。
  外地主机、旧格式、损坏记录和无法探测身份的锁一律拒绝，不提供无条件解锁。
- 接管后的完整性校验失败会恢复旧锁；若该回滚本身被再次中断，
  `logs/recovery/.blocked` 会阻止项目继续打开，当前仍需人工诊断。
- 普通打开校验对象路径和大小；异常恢复会额外重算全部正式对象的 SHA-256，
  因此大型项目恢复会产生一次顺序读取成本。
- 当前只允许 v19 到 v20 的加表迁移；更早的发布前测试格式继续明确拒绝。迁移前会备份
  SQLite，迁移中途强杀仍需继续验证。
- 首次发布前曾生成的旧 v9 POC 若仍使用 `point_set_assets`，打开时会明确提示重建；
  不会把目标资产静默改写为策略 Role。
- Artifact 直接引用图已扩展到 Source、SceneRevision、RegionSet、PointSet 与运行快照；
  canonical manifest 的间接子引用由 Core 解析后作为保护集合传入 retention 事务。
- v10 retention sweeper 采用小批次事务删除；被正式领域事实保护的过期 Job 会转为
  audit。Scale 吞吐与清理进程强杀仍是后续门禁。
- Assembly member 使用结构化外键保护叶子 Source；Assembly 不解析 manifest
  才能判断引用关系，且首版拒绝嵌套和不可逆矩阵。
- RegionSet 当前提供集合读取和成员枚举；ProjectStore 不持久化空间索引。
  `ProjectWorkflowState` 只保存当前 RegionSet 指针，不承担 Viewer 查询；Studio 的视窗裁剪
  由独立 Studio Service 只读投影承担。
- PointSet 当前提供父子谱系、包围盒和 Role 计数；ProjectStore 不持久化逐点空间索引。
  当前可编辑预览由工作链的 Plant Run 指针间接确定；已确认点位只通过当前组合引用，
  Studio Service 可以从各不可变 Artifact 构建临时查询索引。放弃预览只以 compare-and-set
  清除匹配的当前 Plant Run 指针，并明确拒绝已经登记为 `PlantingBatch` 的执行。
- Job 状态机属于 Core；本包只执行原子持久化与 compare-and-set。

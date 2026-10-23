# verdflow-core

ADR-0016：PlantRun 提交时冻结可选 `profile_snapshot`，分配和重试使用该不可变方案，
避免用户更新策略绑定后改变进行中的任务。实际策略版本和包 hash 仍精确冻结；
Core 不替 UI 自动选择新版本。旧执行请求缺少此字段时保持原读取行为。

`AssignmentPreviewService` 提供独立于策略执行的无状态搭配解析，复用 Planting Resolver；
不查询库、不建立 PointSet 或项目任务。参考半径的组合与绘制属于 Studio（ADR-0010）。

按 ADR-0005，手动搭配可不指定 Catalog/Profile，但必须提交自包含候选。Core 调用
Planting 校验并冻结 `draft` 来源，不维护多库聚合实体，不要求保存临时方案。

状态：Experimental  
架构分类：Application Core  
维护责任：Core runtime maintainers

## 职责

负责 VerdFlow 用例编排和持久 Job 状态机。首个纵向切片提供提交、领取、
lease 心跳、进度事件、取消、完成、失败、超时恢复和按 Worker 支持的命令类型
过滤领取；`SceneImportService` 将已验证且未偏移的场景检查结果定版为
SceneRevision；`RegionSetImportService` 统一校验并幂等定版 RegionSet 输出，类型化的
`RegionExtractionImportService` 负责 Blender 等既有场景处理结果的入口校验；
`RegionExtractionService` 冻结精确规则包，并按组合根注册的
Provider command type 提交目标中立的区域提取 Job；`RegionSourceService` 允许已经能够
直接提供 Polygon 的 Source Assembly 通过类型化 Provider 一次形成 SceneRevision 与
RegionSet，不伪造材质事实；`PointSetImportService` 将策略生成点和 Blender 高程
采样结果分别定版为不可变父子 PointSet；`ProjectSnapshotService` 校验目标中立
PointSet、有效绑定和逐点资产分配的一致性，并将其定版为项目运行证据。
`JobRetentionPolicy` 在提交时冻结 temporary/candidate 的保留期；
`JobRetentionSweeper` 以小批次编排过期候选、孤立快照和 Artifact 的引用安全清理。
它同时解析正式 SceneManifest 的子 Artifact 引用，防止材质事实等 canonical 子产物被
误判为孤儿；ProjectStore 只接收保护集合，不承担 manifest 业务解析。
`MaterialAnalysisService` 从 SceneRevision 冻结 MaterialFacts 引用、Provider、实际
model 和 prompt policy，提交 candidate retention Job，并在读取结果时复核候选谱系和
输入材质一一闭合；请求冻结 `manual/automatic` 确认策略，定版时生成精确材质规则包
并将接受的候选提升为审计证据。具体 API 或 Agent 协议仍由 Adapter 持有。
`MaterialSelectionService` 将 MaterialFacts 转为不暴露 Artifact 的产品只读视图，并对
用户提交的完整分类执行缺失、未知、重复和可种植闭合校验；通过后只生成精确材质名
RulePack。Material Flow 的 `ai_assisted` 与 `manual` 起始模式共享该确认和提取链路，
手动模式不创建模型 Job，也不要求模型 Provider。
`ScenePreparationExecutionService` 在不复制阶段算法的前提下，将已有 Scene Inspection
Job 和持久 Material Flow 组合为 Source-to-Region 产品流程；父记录只持有请求、阶段和
子事实引用，支持人工确认暂停、取消与项目重启恢复。

`ApplicationFacade` 将上述能力收敛为 transport-neutral 用例入口；
`WorkerRegistry` 维护命令类型到 Worker Driver 的唯一归属；`ServiceLoop` 在
ProjectStore 所有者线程依次完成遗留进程收敛、过期 lease 恢复、Worker 执行和
到期维护任务。`MaintenanceScheduler` 不创建线程，按独立周期驱动 retention 与
低频 object GC，失败使用有上限的指数退避。
`StrategyExecutionAuthorizer` 通过只读 User Library/Trust 端口校验精确 package
hash；`PlantingGenerationService` 校验 Scene/Region/选择集合，按 Region ordinal
规范化选择并提交持久策略生成 Job；`AssetCatalogLibraryPort` 与
`RoleBindingProfileLibraryPort` 读取精确保存版本。项目快照不能提供或继承用户信任。
`ElevationSamplingService` 校验生成 PointSet、Scene、Source 和 CoordinateFrame，
再按组合根注册的 Provider command type 提交统一高程命令；Core 不识别 Blender、
DTS 或其本机 locator。
`AssetAssignmentService` 从项目冻结策略包读取规范策略，按用户明确指定的当前 Catalog
或自包含 Profile，加上可选 runtime overrides 生成 EffectiveBindingSnapshot，
再提交确定性分配 Job；`AssetAssignmentWorker` 只读取不可变项目 Artifact，不调用
平台 SDK，也不把目标资产写回 PointSet。
资产分配的 control signal 在现有进度检查点协作中断，由 Worker 所属线程先登记
Job 取消中再确认取消；不继续处理全部剩余点位，不以租约到期代替主动取消。
单次 Artifact 解析、hash 和持久化仍为同步操作，不承诺这些步骤可以逐指令打断。
`OutputExportService` 校验高程 PointSet、AssignmentSet、EffectiveBindingSnapshot
谱系和 Provider 版本，再提交 provider-neutral 导出 Job；目标格式由 Adapter 持有。
按 ADR-0007，已接受不可变数据在后续阶段只复核必要引用和一致性。高程定版对照已验证的
生成父点集验收高程增量，不重新完整扫描父点集和高程文档的 JSON Schema；位置有限性、
命中语义、数量、顺序及所有固定种植字段保持约束。策略原始输出仍完整验收。
组合输出使用同一服务的有序多输入请求，逐批校验当前 Composition generation、Region 批次、
高程父子谱系和资产筛选，再由一个 Adapter Job 生成单一输出；Core 不读取或拼接旧 XML。
`CompositionOutputExecutionService` 将项目式工作台的输出作为持久操作推进：仅在用户提交
最终输出时按需补齐各批高程，全部就绪后提交一次组合导出，并支持 CoreHost 重启后继续推进。
ADR-0004 / ADR-0004 / ADR-0004：生成请求冻结 Profile 角色开关并纳入命令 hash，策略自行决定关闭
角色后的构图，Core 始终调用 planting 纯函数移除关闭角色，不探测策略能力，不调整
保留角色点位或私有参数。过滤等价只是策略可选承诺。输出可携带
`selected_asset_ids`，同步筛选点位与 assignment，
选择写入确认/导出命令，导出计数按子集校验。源 PointSet 不被重写，Core 不承担图例计算。
ADR-0007：单点集导出可显式设置 `require_terrain_hit`（默认 false），需要 Provider 声明
`supports_terrain_hit_selection`。命中筛选与资产筛选取交集，预期数量冻结在导出命令，
零命中不创建导出。该能力由实时种植启用，不改变普通工作台的默认行为或采样谱系约束。
`PlantRunExecutionService` 将完整 Plant Run 保存为轻量持久 read model。
提交时使用 planting 的纯函数将 Profile 可选参数合成到本次显式参数之下，并冻结到请求。
随机种子仍只属于运行；历史恢复读取请求，不重新读取当前预设。见 ADR-0006。
该服务由 `ServiceLoop` 的有界 application task 幂等推进生成、资产映射、高程和输出；它只引用
现有 Job 与领域结果，不复制 Artifact，也不构造通用 DAG。同步 Plant Run 仍由 CoreHost
作为 CLI 便利入口组合相同阶段。
`load_plant_run_execution_request()` 只依赖 execution 查询与 Artifact 读取端口，供
只读查询线程恢复不可变种植输入；它与 Coordinator 共用 hash、schema 和请求校验，
不要求持有可写 Store 或等待策略执行线程。

该包不实现 SQLite、HTTP、SSE、Blender、Agent Provider、DTS 或种植算法。

## 公共入口

其他 package 只从 `verdflow.core` 导入：

```python
from verdflow.core import (
    ApplicationFacade,
    AssetAssignmentRequest,
    AssetAssignmentService,
    AssetAssignmentWorker,
    CommandDocument,
    ElevationProviderDefinition,
    ElevationSamplingRequest,
    ElevationSamplingService,
    ExportProviderDefinition,
    JobRuntime,
    JobRetentionPolicy,
    JobRetentionSweeper,
    MaintenanceScheduler,
    MaterialAnalysisRequest,
    MaterialAnalysisService,
    ModelAnalysisProviderDefinition,
    OutputExportRequest,
    CompositionOutputExportInput,
    CompositionOutputExportRequest,
    OutputExportService,
    StrategyExecutionAuthorizer,
    StrategyTrustPort,
    UserLibraryPort,
    PointSetImportService,
    PlantingGenerationRequest,
    PlantingGenerationService,
    PlantRunExecutionRequest,
    PlantRunExecutionService,
    ProjectSnapshotService,
    RegionExtractionProviderDefinition,
    RegionExtractionRequest,
    RegionExtractionService,
    RegionExtractionImportService,
    RegionSetImportService,
    RegionSetProducerDefinition,
    RegionSourceImportService,
    RegionSourceProviderDefinition,
    RegionSourceRequest,
    RegionSourceService,
    SceneImportService,
    ServiceLoop,
    SystemClock,
    WorkerRegistry,
)
```

Job 数据使用 `verdflow.contracts`；持久化通过 `ProjectStore` 公共门面完成。

## 依赖边界

- 依赖 `verdflow-contracts`、`verdflow-project-store` 和纯领域包
  `verdflow-planting`。
- 不依赖 Apps、Bridges、具体 Worker Adapter 或第三方 SDK。
- Worker 通过 lease 调用 Runtime，不直接打开 `project.db`。
- HTTP、SSE 和 WebSocket 只能作为 Runtime 之外的传输层。

## 内部结构

```text
clock.py        可替换时间源
application.py  transport-neutral 用例门面与命令 Artifact 提交
asset_assignment.py 精确 Catalog/Profile 绑定、Assignment Job 提交与结果定版
assignment_worker.py Core 内确定性逐点资产与缩放解析
elevation_sampling.py Provider 注册、PointSet 谱系校验与幂等高程 Job 提交
exporting.py     Provider 中立单批/组合输出校验、Job 提交与 ExportRecord 定版
job_runtime.py  Job 状态机与 lease 编排
material_analysis.py 材质分析 Provider 注册、候选 Job 提交和结果谱系校验
retention.py    Core 所有的 Job 保留期策略
retention_sweeper.py 小批次引用安全清理编排
point_import.py 规范点位、高程语义、父子版本校验与 PointSet 定版
planting_generation.py 策略生成请求校验、规范命令与幂等 Job 提交
plant_run_execution.py 持久线性 Plant Run 状态机、恢复和阶段引用
project_snapshots.py 运行快照交叉校验与 Artifact 定版编排
region_import.py 通用 Region 结果校验与 RegionSet 定版
region_extraction.py 精确规则包冻结、Provider 注册与区域提取 Job 提交
region_extraction_import.py Region Processor 结果入口校验
region_providers.py RegionSet Producer 的共享执行定义
region_source.py 直接区域 Source 的 Provider 注册与 Job 提交
region_source_import.py 直接区域结果的 SceneRevision 与 RegionSet 联合定版
scene_import.py 场景检查结果校验与 SceneRevision 定版
workers.py      Worker Driver 协议、能力归属与取消路由
service_loop.py 单项目所有者线程恢复与执行循环
maintenance.py 同线程周期调度、续批、失败退避与最近执行状态
project_maintenance.py 项目 retention 与低频 object GC 任务
library.py      Strategy 文档、Trust、Catalog/Profile 的只读 Core 端口
strategy_access.py 精确 package hash 的 trusted-local 执行授权策略
__init__.py     显式公共导出
```

## 验证

在仓库根目录运行：

```powershell
python -m unittest discover -s tests\unit -v
python -B tools\job_runtime_recovery_probe.py
```

## 当前限制

- `ServiceLoop.run_until_terminal(timeout_seconds=None)` 可显式无限等待，但仍检查停止
  意图、推进 Worker、Job 续租恢复和维护循环。有限正数仍只限制调用方等待，不替代
  Adapter 的执行上限；通用默认等待预算不变，策略生成入口显式选择无限等待。
- Core Service Loop 必须运行在打开 ProjectStore 的所有者线程；当前不支持跨线程
  共享 Store。HTTP/IPC 需要把请求排队到该线程，不能关闭 SQLite thread check
  直接共享 connection。
- Blender 与 Strategy supervisor 位于独立 Adapter；Local API 已提供 Plant Run 异步
  提交和状态补读，事件推送尚未实现。
- 材质分析已有持久 Job、候选校验、手动/自动确认策略和精确规则包定版；
  `MaterialFlowExecutionService` 以轻量持久状态机自动续接 Region Extraction，人工模式
  可暂停和恢复，不建立通用工作流引擎。
- 高程命令支持 Provider 注册，Blender 与 DTS 实时链路各自通过 Adapter 执行。Provider
  options 必须是 JSON，且不能携带 `*_path`/`*_paths` 本机路径字段。
- `awaiting_user` 已进入公共状态枚举，但尚未实现暂停和恢复命令。
- 自动重试只处理 lease 超时的 `running` Job，不自动重试业务 `failed`。
- 当前 ProjectStore 为单写入者，Worker 必须由 Core 转交持久化操作。
- User Library/Trust 已有独立 LibraryStore 与持久 schema；SystemHost 已实现独立
  Library owner thread 与每项目 CoreHost owner thread。Project Core Host 仍不得直接
  拥有全局 SQLite，只通过只读端口查询。
- 尚未实现 active RegionSet/PointSet 选择和 Region 编辑命令的统一编排。
- 资产分配只接受调用方明确给出的当前 Catalog 或自包含 Profile。Core 不读取 active 指针，
  并在执行前冻结 Catalog generation、内容 hash 或 Profile hash 及稀疏资产事实；当前没有
  自动资产推荐或跨目标 Catalog 合并。
- `PointSetImportService` 只接收目标中立 PointSet v2；旧点位格式不属于 Core
  兼容边界。生成 PointSet 定版同时要求并冻结精确策略包 Artifact。
- Retention sweeper 已接入 Core Host 同线程周期调度，并与低频 object GC 分离；
  尚未完成 Scale 数据和维护阶段进程强杀门禁。调度状态当前只保存在 Host 内存，
  不跨重启保留历史。

## 参数示例

`StrategyPreviewService` 沿用精确策略授权及公开参数校验，通过 `StrategyPreviewPort`
执行策略包持有的单一样本。该短暂用例不创建项目或 Job，不持有几何和生成器实现。
协议见 `docs/architecture/STRATEGY_PARAMETER_PREVIEWS.md`。

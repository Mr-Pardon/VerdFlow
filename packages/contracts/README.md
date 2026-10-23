# verdflow-contracts

策略版本入口：`require_strategy_version` 校验 SemVer，
`stable_strategy_version` 提取可自动升级的正式版本，
`strategy_version_accepts` 判定精确匹配或同 major 向上兼容（ADR-0016）。
这些纯函数不选择安装包、不授予信任、不判断算法语义。

临时搭配展示使用 `AssignmentPreviewRequest/Result`，仅交换稳定点位身份、候选和缩放，
不含尺寸、空间坐标或目标 locator。参数示例结果的可选 `point_ids` 与点序对应，不是项目事实。
`parse_strategy_preview_request` / `parse_assignment_preview_request` 共用传输文档的类型化
解码；授权、语义和预算仍由 Core/Planting 与宿主各自负责，不依赖任一 HTTP 应用。

状态：Experimental  
架构分类：Public Contracts  
维护责任：Core contract maintainers

## 职责

定义 VerdFlow package、进程和应用端之间共享的版本化值对象。该包只描述数据
含义，不实现数据库、文件系统、HTTP、Blender、DTS 或种植算法。

## 公共入口

其他 package 只从 `verdflow.contracts` 导入：

```python
from verdflow.contracts import (
    ArtifactRef,
    ASSET_ASSIGNMENT_COMMAND,
    ELEVATION_SAMPLING_COMMAND_SCHEMA,
    GarbageCollectionResult,
    JobInfo,
    JobLease,
    JobRetentionClass,
    JobRetentionSweepResult,
    MaterialClassificationCandidate,
    MATERIAL_CLASSIFICATION_TASK,
    PlantingRunInfo,
    PointSetInfo,
    PLANTING_GENERATE_POINTS_COMMAND,
    ProjectInfo,
    RegionSetInfo,
    RegionSummary,
    SourceAssemblyMemberInfo,
    SourceAssemblyMemberSpec,
)
```

`__all__` 是当前 Python 公共入口。子模块路径不作为跨 package 稳定 API。

`StrategyPreviewRequest/Input/Result/Operation/Sample` 描述 ADR-0010 的短暂参数示例。
示例结果为 `verdflow.strategy-example-preview` v1，不是项目 PointSet，不能直接采样或导出。

## 依赖边界

- 允许依赖 Python 标准库。
- 不依赖任何其他 VerdFlow package。
- 不包含第三方平台类型或持久化模型。
- 新增公共类型时同步评估 schema 版本和跨进程序列化需求。

## 内部结构

```text
artifacts.py  Artifact 与垃圾回收结果契约
assignment_jobs.py 目标资产分配 Job 的共享 schema 与版本身份
elevation_jobs.py 高程采样 Job 的共享 schema 与版本身份
inference.py  模型/Agent Provider、分析任务、候选和调用 provenance
material_flow_executions.py 材质分析到 RegionSet 的持久执行状态
material_selection.py MaterialFacts 产品投影、选择模式和完整分类请求
scene_preparation_executions.py Source 检查到 RegionSet 的产品流程状态
jobs.py       Job、Attempt、Lease 与 Event 契约
points.py     PlantingRun、PointSet 身份、父子版本和资产计数摘要
planting_jobs.py 策略生成 Job 的命令类型、schema 与版本身份
projects.py   Project 只读信息契约
regions.py    RegionSet 身份、来源和可查询 Region 摘要
scenes.py     CoordinateFrame、叶子/Assembly Source 与 SceneRevision 契约
workers.py    外部 Worker 进程 provenance
__init__.py   显式公共导出
```

## 验证

在仓库根目录运行：

```powershell
python -m unittest discover -s tests\unit -v
```

## 当前限制

- 当前包含 ProjectStore 与 Job Runtime POC 所需类型。
- RegionSet 契约已进入实现；完整几何仍通过 Artifact 引用，不进入值对象。
- PlantingRun 与 PointSet 契约已进入实现；完整逐点数据通过 Artifact 引用。
- Source Assembly 成员、显式 source-to-scene 矩阵和 Region member provenance
  已进入实现；首版只允许一层 Assembly。
- Strategy、目标 Asset 和 Assignment 文档由 `verdflow-planting` 持有；ADR-0005
  已 Accepted，策略生成 Job 与 Provider 中立高程采样的跨进程命令身份已进入本包。
- ExportRecord、PlantRunExecution、MaterialFlowExecution 与 ScenePreparation
  契约已进入实现。
- PlantRunExecution request v2 支持自动直通和持久 Review 确认；确认参数使用独立
  continuation Artifact，不修改原始运行请求。
- `awaiting_user` 已定义状态但尚无对应命令契约。
- Job retention class、冻结期限、清理时间和单批 sweep 结果已进入公共契约；调度
  周期不属于 Contracts。
- 材质分析任务、三类 Provider 协议、候选结果和持久人工/自动确认流程已接入。
- 尚未生成 TypeScript 对应类型。

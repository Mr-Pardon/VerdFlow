# verdflow-planting

状态：Experimental  
架构分类：Domain Capability  
维护责任：Planting contract and resolver maintainers

## 职责

实现 Phase 2A 的目标无关种植与目标资产分配契约：受限 YAML 作者格式、版本化
Schema、Canonical JSON、semantic hash、EffectiveBindingSnapshot 构建、稀疏
TargetAssetUseSnapshot 提取，以及固定 seed 的确定性 AssetAssignmentSet 生成。

该包不执行几何撒点、高程采样、数据库持久化、DTS XML 导出或 Blender 实例化。
`resolve_assignment_preview` 只为临时展示复用现有候选/缩放解析规则，接受稳定 ID 和
候选策略，不接受半径、坐标或目标 locator，不生成正式 PointSet；参考范围由 Studio 绘制。
Strategy Role 语义由策略持有，同 major 的正式版本承诺向后兼容；DTS 与 Blender
Catalog 保持独立事实，见 ADR-0016。

ADR-0005：`catalog_binding_candidate` 产生来源限定的稳定候选，原始 ID 留在来源链接；
`build_binding_draft` / `build_effective_binding_from_draft` 校验并冻结未保存的多库搭配，
不创建 Catalog/Profile。`profile_from_binding_draft` 只在显式保存时构建 Profile；元数据
同步按每项来源的原始资产 ID 定位，旧候选 ID 保留。草稿和 Profile 共用候选校验规则。

策略可通过可选的 `generation_parameters` 声明数值参数契约。本包校验声明的唯一键、
有限数值与默认值范围，不负责生成控件或执行参数；实际运行值经 `generation_args` 交给
生成器最终校验。Profile 可选 `strategy_parameters` 只保存公开参数值，不复制定义；
`profile_generation_args` 将预设合成到显式运行覆盖之下，不更改输入。字段缺省表示纯搭配，
空对象表示全部默认值的预设。边界见 ADR-0004 / ADR-0006。
公开参数可选 `visible_for_roles` 声明角色展示关联，本包只校验有效角色引用；应用在关联
角色全部关闭时隐藏控件，不改变执行值。见 `STRATEGY_PARAMETER_PRESENTATION.md`。

## 公共入口

其他 package 只从 `verdflow.planting` 导入：

```python
from verdflow.planting import (
    PlantingDocumentError,
    build_effective_binding_snapshot,
    canonical_json_bytes,
    extract_target_asset_use_snapshot,
    load_catalog_yaml,
    load_profile_yaml,
    load_strategy_yaml,
    prepare_asset_catalog_document,
    prepare_role_binding_profile_document,
    resolve_asset_assignments,
    semantic_hash,
    validate_point_set_v2,
)
```

输入输出均为 JSON-compatible Python 对象。调用方负责把规范文档登记为 Artifact；
本包不读取 ProjectStore，也不决定项目生命周期。

Profile 保存策略版本基线，并完整保存目标 locator、显示快照、权重和缩放。
`validate_binding_strategy` 校验同策略、同 major 向上版本及角色/参数；
`complete_profile_assignments` 将未配置的可选角色补为关闭，`validate_profile` 校验冻结方案。
有效绑定的策略身份来自实际执行版本，来源 semantic hash 仍来自未改写的原方案。
Catalog 的可选 `reference_radius_m` / `reference_height_m` 均为缩放 `1.0` 时的有限正数
米制参考尺寸。创建搭配时复制、显式同步时更新、运行快照中冻结；它们不改变资产身份、
策略点位或缩放解析，见 ADR-0005 / ADR-0005。
每个角色映射支持 `enabled`（默认 true）；关闭保留候选，允许空候选，不能全部关闭。
`selection.py` 提供无重采样的角色点位过滤与输出资产选择，语义见 ADR-0004 / ADR-0004。
角色开关后的重新构图由策略包持有，本包的后过滤只移除关闭角色，不重排保留点位。
ADR-0007：`select_persisted_output_assets` 仅用于已由 Core 接受的不可变文档，调用方传入
Core 的点数及 assignment hash，复核文档头和引用，不再全量 Schema/重算原文档 hash。
无变化筛选原样返回；真正生成子集仍保持规范文档及语义 hash。原始文档入口继续严格校验。
`validate_elevation_point_set` 对照已验收父点集检查高程增量：固定字段/顺序/数量不可改变，
仅复核采样结果、有限坐标、命中/未命中语义和未偏移状态，不重复校验父点集所有字段。
Catalog 只在作者流程中提供可选来源信息，不参与执行门禁。EffectiveBindingSnapshot
记录本次 Profile 或当前 Catalog 来源，TargetAssetUseSnapshot 冻结真正使用的精确资产记录。

## 依赖边界

- 允许依赖 `verdflow-contracts`、Python 标准库、PyYAML 和 jsonschema。
- 不依赖 `verdflow-core`、`verdflow-project-store`、Apps、Bridges 或 Adapter。
- 不导入 Blender、DTS 或其他目标 SDK 类型。
- Provider-specific 字段只能位于 Catalog 的 `provider_metadata` 中。
- YAML 只用于人工作者文档；PointSet、Snapshot 和 AssignmentSet 使用规范 JSON。

## 内部结构

```text
authoring.py    受限 YAML 1.2 解析和作者文档入口
canonical.py    Canonical JSON 与 semantic hash
documents.py    已验证 Catalog/Profile 到持久化 command DTO
effective.py    Profile/override/Catalog 到有效绑定快照
snapshots.py    从有效绑定提取精确稀疏 Catalog 使用快照
resolver.py     跨运行确定性资产选择和目标缩放
validation.py   版本化 JSON Schema 校验与领域不变量
schemas/        Phase 2A 版本化文档 Schema
```

## 验证

在仓库根目录运行：

```powershell
python -m unittest tests.unit.test_planting_phase2a -v
python -m unittest discover -s tests\unit -v
```

## 当前限制

- ADR-0005 已 Accepted；文档契约、ProjectStore v10 快照持久化、正式 v2
  PointSet Job 入口、retention 门禁与真实 Blender Catalog 探针已实现。
- 仅支持精确 StrategyVersion、自包含 Profile 或显式当前 Catalog 映射、
  `fixed`/`uniform_random` 等比缩放和 JSON 输出。
- Resolver v1 使用 Canonical JSON、SHA-256 前 64 bit 和十进制累计权重；缩放按
  9 位小数 half-even 固化。其他语言实现前必须增加共享 golden vectors。
- 不实现物理尺寸到目标模型倍率的校准。
- Asset Collection 只在构建 Snapshot 时展开，不作为 Assignment 运行时依赖。
- PointSet v2 是唯一受支持的点位格式；本包不提供旧格式导入。
- Catalog/Profile 已进入 LibraryStore；Project Snapshot 已进入 ProjectStore v10，
  candidate retention sweeper 已实现；Viewer 分块和 Agent 候选确认尚未实现。

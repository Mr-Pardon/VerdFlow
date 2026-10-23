# VerdFlow Strategy Packs

状态：Experimental

本目录保存可拔插的种植策略包。每个策略包拥有自己的空间生成器、作者态
`strategy.yaml` 和维护说明，但不得依赖 DTS、Blender 或其他目标平台资产 ID。

策略包不是 Python workspace package，不参与 VerdFlow package 依赖图。Core 后续
通过 `trusted-local` Strategy Worker 加载它们；当前执行绑定精确 package hash，
不代表可以安全运行未经审核的第三方 Python。

当前迁移包：

- `universal-street-tree`：通用行道树。
- `universal-natural-grove`：通用自然聚落。

- `layered-ecological-community`：层次生态群落；`1.0.0` 使用四角色连续密度场，
  `2.0.0` 使用地表基质与主景结构两角色共享密度场；`layered-ecological-community-v2`
  当前为 `2.2.0`，关闭铺底后主景覆盖比例以整个 Region 为基准。
- `lawn-feature-groups`：草坪点景组团，连续草坪上叠加靠内分布的复合点景组团。

每个策略包至少需要：

- `README.md`：设计意图、职责边界、生成入口和验证方式。
- `strategy.yaml`：版本化策略、Role、空间参数和弱约束地形信息。
- `generator.py`：只生成目标无关空间事实，不分配平台资产或执行导出。

角色名称、说明和可选的用途分组、搭配建议、构图示意均由策略包持有。展示声明使用
`strategy.yaml` 的 `extensions.role_presentation`，随 `.vfs` 分发；应用只做通用读取与
渲染，不维护策略 ID/hash 到资料的专属表。作者格式见
[用途展示契约](../docs/architecture/STRATEGY_ROLE_PRESENTATION.md)及 VFS Author 的配套参考。

角色开关后的构图规则同样由策略包持有，按 ADR-0004 接收 `excluded_role_keys`；Core
始终过滤关闭角色。`filter_equivalent` 是可选承诺，不是所有策略必须遵守的算法限制。

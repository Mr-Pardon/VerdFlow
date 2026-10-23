# 草坪点景组团

状态：Accepted baseline，可安装执行

策略版本：`1.0.2`，增加单一弧形内凹花坛参数示例，生成算法不变。

`lawn_feature_groups@1.0.2` 在 Region 内生成双层连续草坪和少量复合点景组团。
空间算法沿用已通过 Studio / DTS 真实场景验证的基线，并遵循正式策略包边界：

- 输入：一个或多个 Region、seed 和策略参数覆盖。
- 输出：目标无关 PointSet v2。
- 资产：由独立的 Role Binding Profile 提供。
- 高程与导出：由后续 Adapter 处理。

五个 Role：

- `lawn_groundcover`：固定退距后的主体草坪。
- `edge_groundcover`：以更小中心距覆盖退化带的收边草坪。
- `feature_anchor`：中心主景。
- `surrounding_shrub`：组团灌木。
- `outer_accent`：外围点缀。

本补丁仅调整角色表达，并在 `strategy.yaml` 的 `extensions.role_presentation` 中携带
用途分组、搭配建议与构图示意；不改空间算法。展示资料随 `.vfs` 分发，应用只通用渲染。
新包需按精确 hash 重新导入和信任，不替换已有项目冻结的策略或自动更新 Profile。

设计与算法边界见 [DESIGN.md](DESIGN.md)。

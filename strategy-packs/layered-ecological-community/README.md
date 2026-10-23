# 层次生态群落

状态：Accepted baseline，可安装执行

策略版本：`1.0.0`

## 设计意图

在同一个面状绿地 Region 内叠加四个相关但不互斥的连续密度场，形成上层植物冠幅
高值区、自然稀疏过渡和地表植物覆盖。该方式不切分子多边形，也不使用圆形聚落
边界，因此疏密变化是连续的。

四个必需 Role：

- `canopy_anchor`：上层主导植物。
- `medium_shrub_structure`：中层结构植物。
- `herbaceous_companion`：下层伴生植物。
- `low_shrub_matrix`：地表覆盖植物。

所有 Role 使用 `weighted_pool`，具体资产、候选权重与缩放仍由 Profile 或本次临时映射决定。

## 基准参数

`strategy.yaml` 固化了已通过真实地形视觉验证的默认组合，并通过顶层
`generation_parameters` 声明名称、范围、步长、展示分组和结果导向说明。Studio 根据
该契约生成通用调参面板，不识别下列参数键：

| 参数 | 默认值 | 作用 |
| --- | ---: | --- |
| `overall_density` | `0.26` | 四个 Role 共用的整体密度倍率。 |
| `canopy_density` | `3.60` | 上层主导植物的点数密度倍率。 |
| `canopy_spacing_scale` | `0.68` | 上层主导植物目标间距与最小间距缩放。 |
| `canopy_field_contrast` | `2.70` | 上层植物高低密度区的对比。 |
| `shrub_density` | `0.45` | 中层结构植物密度。 |
| `layer_blend` | `0.58` | 四个层次共享空间趋势的程度。 |

连续疏密起伏的尺度固定使用已验证默认值 `1.25`，不作为用户参数暴露。

## 职责边界

- 策略包只生成未偏移、未采样高程的 `verdflow.points` v2。
- 每个 Region 独立派生密度场与子 seed，RegionSet 只负责批量输入。
- 不持久化密度场、斑块边界或中间子 Region。
- 不包含 DTS、Blender 或具体植被资产 ID。
- Core 继续负责包信任、精确哈希、执行编排与 PointSet 定版。

## 执行与验证

生成入口是 `generator.py:generate`，由 `trusted-local` Strategy Worker 按精确包哈希调用。

```powershell
python -m unittest tests.unit.test_layered_ecological_pack -v
```

训练可视化与项目采样工具保留在 `feature/layered-ecological-planting` 分支，不进入正式策略包。

当前测试覆盖契约与生成器默认值一致、参数覆盖、固定 Seed 的确定性、Region 边界与孔洞、
四个 Role 输出及默认 DTS 映射。Studio 与 DTS Live WebUI 均已接入通用参数面板。
Role 数量上限与小面结构化诊断仍是后续候选能力，不属于 `1.0.0` 已实现范围。

性能边界：下层伴生场目前逐个检查上层植物位置，大面或高密度输入可能增加候选计算成本。
本轮主线回归以合成小面验证契约，不代表全域规模性能验收；优化前应单独测量，避免改变
已确认的空间效果。各 Role 的间距约束独立，不承诺不同 Role 之间无重叠。

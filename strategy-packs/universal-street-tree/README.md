# 通用行道树

状态：Experimental migration

策略版本：`2.0.2`；本补丁增加单一 160 × 6 米道路绿带参数示例，不改生成算法。
在 `strategy.yaml` 的
`extensions.role_presentation` 中携带用途分组、搭配建议与构图示意，不改空间算法。
资料随 `.vfs` 分发，应用只通用渲染。新包需按精确 hash 重新导入和信任，不替换已有项目
冻结的策略或自动更新 Profile。

## 设计意图

用于道路分隔绿带、道路附属带状绿地和主轴明确的狭长区域。生成器沿局部截面中心
形成可随弯曲和收缩变化的中心路径，并按局部宽度切换灌木排布：

- 宽度 `<= 1m`：中心单行灌木。
- 宽度 `(1m, 4m)`：左右双带灌木。
- 宽度 `>= 4m`：外侧、中心、外侧三带灌木。
- 所有宽度模式都沿中心路径等距放置乔木点缀。
- 所有植被点位使用独立随机流生成可复现的 Z 轴随机朝向，不改变点位位置。

## 职责

`generator.py` 只生成六种策略本地 Role、未偏移位置和旋转，不选择植物资产、
不生成缩放、不做高程采样或导出。DTS 的静态网格体与缩放范围由独立
`asset-packs/dts-default` Profile 提供。

## 生成入口

```python
from generator import generate

point_set = generate(
    {
        "strategy": strategy_document,
        "selected_regions": selected_regions,
        "seed": 2026071301,
    }
)
```

输入 Region 使用现有 Blender Worker 的未偏移 `rings` 或
`footprint_boundary_xyz`。输出为 `verdflow.points` v2。

## 角色

| Role | 空间职责 |
|---|---|
| `narrow_center_shrub` | 单行模式中心灌木 |
| `double_left_shrub` | 双带模式左侧灌木 |
| `double_right_shrub` | 双带模式右侧灌木 |
| `outer_shrub` | 三带模式两侧灌木 |
| `middle_shrub` | 三带模式中心点缀灌木 |
| `tree_accent` | 所有模式均存在的等距乔木 |

## 验证

在仓库根目录运行：

```powershell
python -m unittest tests.unit.test_universal_street_tree_pack -v
```

回归验证固定输入下的点位、角色和资产映射；算法行为变化须遵循策略版本与兼容边界。

## 当前限制

- 空间算法沿用 legacy `universal_street_tree_v1`，仍以面几何推导中心路径，不读取
  真实道路中心线。
- 策略通过精确 package hash 信任与外部 Worker 执行；不宣称签名或不可信代码沙箱隔离。
- 真实道路样本需另行验证；源码测试不依赖旧工程或包含原始项目模型及坐标数据。

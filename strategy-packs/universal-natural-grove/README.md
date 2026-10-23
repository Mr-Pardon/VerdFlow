# 通用自然聚落

状态：Experimental migration

## 设计意图

面向非细长范围绿地形成自然协调的聚落：大面拆成多个可读小聚落，相邻小面按局部
社区组团，并先保证可种小面有覆盖。每个聚落以 50/35/15 分配三个构图 Role，避免
只在全局统计上满足比例而局部失衡。

## 职责边界

- 生成未偏移、未采样高程的 `verdflow.points` v2 空间事实。
- Role 只表达本策略内的构图职责，不包含 DTS 或 Blender 资产 ID。
- 不分配资产、缩放和预览颜色，不执行高程、Viewer 同步或导出。

## 生成入口

`generator.py:generate`，由 `trusted-local` Strategy Worker 按精确 package hash 调用。

## 验证

```powershell
python -m unittest tests.unit.test_universal_natural_grove_pack -v
```

## 当前限制

- 合成双面迁移探针已验证 80 个点的位置、旋转和聚落组与旧生成器逐点一致；真实
  历史项目结果门禁仍需补齐。
- 小面组团基于区域中心距离，不推断跨面的景观语义。
- 生成 Z 仅为轮廓代表值，导出前仍必须经 Geometry Worker 完成未偏移高程采样。

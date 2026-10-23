# 夹具构建工具

- `blender_spatial_slice.py` 按显式边界生成保持坐标的 FBX 切片。
- `selection.py` 提供 Source Member、点位窗口和 Region 包围盒筛选的纯函数。

这些工具不进入产品运行链路，不内置项目名、模型路径或业务坐标。
调用方负责输入授权与结果清理，单元测试使用合成数据。

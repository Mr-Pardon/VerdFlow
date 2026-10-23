# verdflow-geospatial-worker

状态：Experimental
架构分类：Integration Adapter
维护责任：Geospatial integration maintainers

## 职责

内置地理空间 Adapter。它在外部 Python 进程中把 SHP/GeoJSON Polygon 数据转换为标准
`RegionSet` 输出，并使用配套 GeoTIFF 为 PointSet v2 采样高程。

本 Adapter 假设用户已经在上游对齐矢量与栅格坐标；不检测 CRS 等价性，不执行重投影、
单位换算或坐标轴修正。详细边界见 ADR-0001 与 ADR-0008。

## 公共入口

`package_shapefile()` 在应用入口把 Shapefile 必要伴随文件封装为确定性 ZIP Artifact；
`GeospatialWorkerSupervisor` 通过外部 Python 进程提供 Region Source 与 Elevation Provider。
SystemHost 在添加 SHP Source 时调用该封装入口，Studio 和 Local API 不处理伴随文件清单。

`export_polygon_shapefile(Sequence[ShapefilePolygon])` 将调用方提供的 XYZ 环导出为
二维 Polygon ZIP，含 UTF-8 DBF/CPG；不重投影、不猜测 CRS、不写 PRJ、不采样高程。
调用方负责选择保存目的地，Adapter 仅返回字节，不依赖 DTS 或 UI。

`import_polygon_shapefile(Path)` 用于轻量选区复用，读取 SHP/ZIP，按 CPG 解码名称，
返回 `ShapefilePolygon` 集合。多部件拆分，带洞面及无效面整份拒绝；此限制仅针对轻量
选区交换，不改变标准 Region Source 对洞的支持。Z 为输入参考高度而非地形采样结果。
读取资源边界与错误语义见 ADR-0007 / ADR-0007（64 MiB、100000 顶点；Connector 草稿 30 MiB）；
不执行数据修复、坐标转换或 CRS 校验。

## 依赖边界

- `pyshp`、`shapely` 由 Adapter 的几何读写入口使用，`rasterio` 用于 Worker；均不进入 Core 包。
- Job 命令只保存 Source/Artifact 引用，不保存用户本地路径。
- Worker 不做 CRS 检测、坐标转换或数据修复。
- 首版仅接受一个 Polygon 矢量源与一个 GeoTIFF 组成的恒等 Source Assembly。

## 验证

```powershell
python -m unittest tests.unit.test_region_source
python -m unittest tests.unit.test_geospatial_processing
python -m unittest tests.unit.test_geospatial_vertical
```

后两个测试需要安装本包声明的地理空间依赖；纵向测试还需要通过环境变量
`VERDFLOW_GEOSPATIAL_TEST_PYTHON` 指向含这些依赖的 Python。

## 当前限制

- 已接入 SystemHost 与 Local API 的类型化 Source Assembly、Region Source 和项目能力注册；
  Studio 已提供开发态创建入口，并在定版后复用标准区域、种植、预览和输出工作台。
- NoData、范围外和无效几何当前使整个 Job 失败，不做静默跳过或填零。
- 当前高程使用指定波段的像元采样，默认第一波段，不计算真实地形法线。
- 合成数据已通过隔离外部进程纵向测试；用户对齐的真实业务样本仍待坐标与视觉验收。
- Windows 便携 Runtime 已锁定 GIS 依赖，但新增原生 Wheel 的包体和干净机器兼容性仍需在
  下一次便携构建中验证。

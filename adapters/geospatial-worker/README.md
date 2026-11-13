# verdflow-geospatial-worker

状态：Experimental
架构分类：Integration Adapter
维护责任：Geospatial integration maintainers

## 职责

内置地理空间 Adapter。它在外部 Python 进程中把 SHP/GeoJSON Polygon 数据转换为标准
`RegionSet` 输出，并使用配套 GeoTIFF 为 PointSet v2 采样高程。
另提供显式启用的 RGB 影像区域识别试验能力，不影响矢量导入和高程采样的依赖。

本 Adapter 假设用户已经在上游对齐矢量与栅格坐标；不检测 CRS 等价性，不执行重投影、
单位换算或坐标轴修正。详细边界见 ADR-0001 与 ADR-0008。

## 公共入口

`write_shapefile_package()` 分块写入项目暂存区，把必要伴随文件封装为确定性 ZIP Artifact；
`package_shapefile()` 是返回内存字节的便捷入口。
`GeospatialWorkerSupervisor` 通过外部 Python 进程提供 Region Source 与 Elevation Provider。
SystemHost 在添加 SHP Source 时调用该封装入口，Studio 和 Local API 不处理伴随文件清单。

`export_polygon_shapefile(Sequence[ShapefilePolygon])` 将调用方提供的 XYZ 环导出为
二维 Polygon ZIP，含 UTF-8 DBF/CPG；不重投影、不猜测 CRS、不写 PRJ、不采样高程。
调用方负责选择保存目的地，Adapter 仅返回字节，不依赖 DTS 或 UI。

`import_polygon_shapefile(Path)` 用于轻量选区复用，读取 SHP/ZIP，按 CPG 解码名称，
返回 `ShapefilePolygon` 集合。多部件拆分，合法内环通过 `holes` 保留，无效面整份拒绝；
不自动填洞或修复。Z 为输入参考高度而非地形采样结果。
读取资源边界与错误语义见 ADR-0021（128 MiB、200000 顶点；Connector 草稿 30 MiB）；
不执行数据修复、坐标转换或 CRS 校验。

`GeospatialDataError(code, message)` 经 Worker/Job 或 SystemHost 传递安全的用户提示。
文件、几何、高程及运行错误分别报告；底层堆栈仅写本地诊断。

## 依赖边界

后续多入口扩展遵守 [Adapter 链路契约](../../docs/architecture/ADAPTER_WORKFLOW_CONTRACT.md)
（ADR-0024）：矢量和影像输入可以同包提供并共享 GeoTIFF 高程能力，采样须显式引用高程源，
输出平台独立。以下固定源对为现有区域准备入口，不是所有新能力的契约。显式采样命令
v2 只加载已绑定的高程 TIF，旧 v1 保持源对逻辑。

- `pyshp`、`shapely` 由 Adapter 的几何读写入口使用，`rasterio` 用于 Worker；均不进入 Core 包。
- SHP 和较大的 GeoJSON 集合按要素转换并直接写入两个私有 v1 产物，读完才核对范围、
  写入计数并登记。小 GeoJSON 使用原生 JSON 整读；不把读法切换阈值当作导入上限。
  自定义隔离解释器须安装 `ijson`（不能只装在用户 site-packages）。
- 高程进程复用 Planting 的有界 PointSet 读取器，按批次采样并保持原有输出字节。
  同一批内按栅格窗口集中取值，保留有限窗口缓存和原有点位顺序。
  便携运行时提供依赖；自定义 Worker Python 必须安装地理适配器的依赖（含 `ijson`）。
  未读完点集或任一高程无效时，不发布部分采样结果。
- Job 命令只保存 Source/Artifact 引用，不保存用户本地路径。
- 矢量入口不做 CRS 检测、坐标转换或数据修复，仍接受一个 Polygon 矢量源和一个高程 TIF。

## 影像区域试验入口

- 宿主调用 `GeospatialExtensionFactory(..., imagery_model_path=...)` 显式提供本地模型，
  才注册 `verdflow.geospatial.imagery` 和 `verdflow.imagery-regions`。`python_executable`
  指向预先配置了 GIS 和 ONNX Runtime 的 Worker 环境；默认环境和便携包不新增依赖。
- `RegionSourceRequest` 使用 `ImageryRegionOptions(member_id, rgb_bands, backend, device_id)`
  的 `to_document()`。输入为恒等 Source Assembly 中显式选中的 GeoTIFF 成员，识别不读取
  高程文件。选用 `cpu` 或 `directml` 后端；不自动切换模型、颜色规则或整个后端。
- `satlens-segformer-b0/1` 仅接受实测 ONNX 文件的固定 SHA-256。原始 512 像素窗口、384
  步长，按重叠概率融合；条带累计避免保存整张概率栅格。模型路径仅在受控子进程命令中出现，
  不进入持久化 Job。运行时不会下载模型、执行训练代码或上传影像。
- 要求带米制投影和有效定位的自包含 GeoTIFF、三个不同的整数 RGB 波段，有效值 0–255。
  不再限制整幅像素数或边长。限制单个解码块 128 MiB，按临时掩膜和对象库空间预检并监测磁盘。
  Windows 影像 Worker 的私有提交内存预算为 3 GiB，监督只在无处理进展超时（默认 300 秒）
  或取消时回收进程；矢量和高程 Worker 同样按实际进展而不是总耗时计时。
- 分条带窗口推理并写出同网格掩膜，按固定瓦片转面，以共享像素边拼接四连通区域。
  Studio 新任务保留全部候选，验收时才按原图区域像素数即时筛选；旧调用的
  `min_region_pixels` 仍支持在候选准备时剔除小面，不改原始掩膜。不重投影、不填洞、不静默平滑边界。
- `submit_region_source()` 只产生候选 Artifact，结果附带同网格 `vegetation_mask` GeoTIFF
  （0 非植被、1 植被、255 NoData），以及带输入哈希、模型、运行时和配置的来源 manifest。
  调用方检查结果后再用 `finalize_region_source()` 定版。调用工作流解析时显式传入
  `recipe_id="verdflow.imagery-regions"` 和高程 `SamplingSourceRef`；两个 TIF 不可按类型猜测。
- Studio 试用路径额外提交 `elevation_member_id`，准备时检查单波段高程、相同投影和覆盖相交；
  识别算法仍只消费 RGB。预览 PNG 最长边 1600 像素，定位使用单独记录的预览仿射变换。
  候选边界按地理位置分批读取，每批最多 256 个区域、6 万边界顶点。精确候选和指标按
  原排序流式写出；小结果直接内存排序，较大结果用项目临时 SQLite 排序，不按累计
  区域或顶点数提前拒绝。单个区域超出分页绘制预算只简化边界或保留在候选列表，
  不修改正式候选或孔洞。单连通区的瓦片碎片累计超过 16 MiB 时分批精确合并，不再因此拒绝；
  普通区域仍一次合并。预览 PNG 保留 24 位编号边界。
  全图候选概览独立于分页细节，使用精确候选生成预览分辨率的区域序号 PNG；不参与定版几何。
  超复杂区域分块写出正式坐标，概览和近景按原始面的像素中心覆盖绘制，不复制整面坐标用于显示。
- 大于 32,768 个候选时，随既有 JSON 索引附加序号对齐的紧凑像素数与边界批次索引。
  两者在同一次候选遍历中写出；不更改区域、指标、掩膜或候选编号。
- 近景几何在 32 MiB 内继续使用内存空间索引。更大的候选由独立瓦片进程逐行创建临时
  SQLite 空间索引，将边界直接编码为紧凑 XY WKB，不构建完整嵌套坐标列表。瓦片只分批解码
  相交几何；单条复杂几何仍需要相应的原生内存。建索引时 Studio 保留
  概览，完成后自动更新。临时索引随瓦片会话关闭清理，不增加正式 Artifact，也不修改旧项目。
  自定义影像 Python 的隔离环境需要可用的 `ijson` 和 SQLite R*Tree。
- Windows 的识别 Worker 根据启动时的可用物理内存和系统提交余量设置 Job Object 额度，
  同一 Host 在并发不足时等待已分配额度释放；仅在系统遥测不可用时使用旧的 3 GiB。
  此额度不是影像尺寸限制，也不改推理设备、输出精度或近景预览进程的独立预算。
- 实验权重许可链尚未确认，不能以此作为生产包随附、自动下载或商用授权的依据。

## 验证

```powershell
python -m unittest tests.unit.test_region_source
python -m unittest tests.unit.test_geospatial_processing
python -m unittest tests.unit.test_geospatial_vertical
python -m unittest tests.unit.test_geospatial_imagery
```

地理空间测试需要安装本包声明的依赖；矢量纵向测试还需要通过环境变量
`VERDFLOW_GEOSPATIAL_TEST_PYTHON` 指向含这些依赖的 Python。

真实影像纵向用例为可选测试：设置 `VERDFLOW_IMAGERY_TEST_PYTHON`、
`VERDFLOW_IMAGERY_TEST_MODEL`、`VERDFLOW_IMAGERY_TEST_SOURCE`、`VERDFLOW_IMAGERY_TEST_MASK`
分别指向本地运行环境、已验证 ONNX、影像和预期掩膜；可通过 `VERDFLOW_IMAGERY_TEST_BACKEND`
选择 `cpu`（默认）或 `directml`。用例比较像素和坐标，确认后定版，再使用合成的对齐高程
验证显式采样绑定。私有测试影像和第三方权重不入库。

## 当前限制

- 已接入 SystemHost 与 Local API 的类型化 Source Assembly、Region Source 和项目能力注册；
  Studio 已提供开发态创建入口，并在定版后复用标准区域、种植、预览和输出工作台。
- NoData、范围外和无效几何当前使整个 Job 失败，不做静默跳过或填零。
- 当前高程使用指定波段的像元采样，默认第一波段，不计算真实地形法线。
- 合成数据已通过隔离外部进程纵向测试；用户对齐的真实业务样本仍待坐标与视觉验收。
- Windows 便携 Runtime 已锁定 GIS 依赖，但新增原生 Wheel 的包体和干净机器兼容性仍需在
  下一次便携构建中验证。

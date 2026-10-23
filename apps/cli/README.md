# verdflow-cli

状态：Experimental  
架构分类：App / Command Line  
维护责任：CLI maintainers

## 职责

提供命令行入口，覆盖项目创建与检查、Source 登记、单位矩阵 Source Assembly、场景
检查、SHP/GeoJSON + GeoTIFF 区域准备、完整 Plant Run、DTS XML 导出以及 Job 查询、事件和
取消。CLI 只负责参数解析、
组合用户 Library 与单项目 Core Host，并输出稳定 JSON。

## 公共入口

安装 workspace 后使用：

```powershell
verdflow doctor
verdflow project create demo.verdflow --name Demo
verdflow source add demo.verdflow scene.fbx
verdflow export dts demo.verdflow POINT_SET ASSIGNMENT_SET output.xml \
  --offset-x 0 --offset-y 0 --offset-z 0
verdflow plant run demo.verdflow output.xml \
  --library user-library --scene-revision SCENE --region-set REGIONS \
  --region REGION --strategy-package-hash HASH --seed 7 \
  --target dts --catalog-id CATALOG --catalog-revision REVISION \
  --profile-id PROFILE --profile-version VERSION
```

Python 调用可使用 `from verdflow.cli import main`。

## 依赖边界

- 只依赖 Contracts、Core Host、Geospatial Adapter 与用户级 LibraryStore 组合入口。
- 不直接依赖 ProjectStore、SQLite 或 Blender Worker。
- 不实现种植、Region、坐标或输出算法。

地理空间纵向入口：

```powershell
verdflow geospatial prepare <project.verdflow> <areas.shp|areas.geojson> <height.tif>
```

该命令只负责将输入注册为 Source Assembly 并调用类型化 Provider，不检测或转换 CRS。

## 验证

```powershell
python -m unittest tests.unit.test_cli -v
```

## 当前限制

- 尚无本地 IPC，命令采用一次性 Host 生命周期。
- `assembly create` 首版成员矩阵均为显式单位矩阵。
- `plant run` 当前采用一次性同步 Host 生命周期；精确阶段 Job 可幂等复用，但 CLI
  退出后没有独立的顶层流程进度对象。

# verdflow-blender-worker

状态：Experimental
架构分类：Integration Adapter
维护责任：Blender integration maintainers

## 职责

发现用户安装的 Blender，在受监督进程中执行场景检查、材质规则区域提取、连通面岛合并、
Region 指标和 BVH 高程采样。Source Assembly 保留成员身份与显式变换；输入和高程结果
使用未偏移坐标。命令、心跳、进度和结果使用独立协议，不从诊断 stdout 推断业务状态。

Blender 按显式配置、`BLENDER_EXE`、标准安装目录和 PATH 查找。场景检查可产生可丢弃的
`.blend` 缓存；缓存缺失或能力版本不匹配时回退重新导入，不改变 Source 的事实地位。

## 公共入口

从 `verdflow.blender_worker` 导入 `BlenderWorkerConfig`、`BlenderWorkerSupervisor`、
`locate_blender` 与公开命令常量。持久命令只保存 Source/Artifact 标识，Supervisor
在 Attempt 临时目录中物化输入、登记产物并清理文件，不暴露对象库内部路径。

## 依赖边界

- Supervisor 使用 Contracts、Core 与 ProjectStore 公共入口；Core 不反向依赖本包。
- 进程树管理复用 `verdflow-worker-runtime`，Blender 协议与能力版本由本适配器持有。
- `bpy` 限于 Worker 入口与 capabilities；纯拓扑算法不依赖 Blender。
- 不实现策略、DTS 输出、绿地业务决策或 Blender 面板；Worker 不打开项目数据库。

## 验证

先按 [开发与测试](../../docs/development/TESTING.md) 配置 Python 源码路径：

```powershell
python -m unittest discover -s tests/unit
python -B tools/blender_worker_probe.py
python -B tools/blender_capability_probe.py
python -B tools/blender_source_assembly_probe.py
python -B tools/blender_asset_catalog_probe.py
python tools/blender_worker_recovery_probe.py
```

真实探针需要本机 Blender，按各命令参数提供有权使用的输入；不依赖预置业务模型。

## 当前限制

- 首次 FBX 导入仍有解析和内存成本；缓存容量配额与 LRU 回收尚未实现。
- 不摄入 Source 外的纹理依赖；纹理引用不保存工作站绝对路径。
- 提取会跳过纯垂直或零 XY 投影面积残片，并记录统计；Core 仍拒绝非正面积 Region。
- 资产检查仅支持标记为 Asset 的 Collection，不负责实例化回 Blender。
- Windows 进程树与父进程退出清理有自动化覆盖，POSIX 的真实运行环境仍需单独验证。
- 运行日志与性能取决于模型、Blender 和机器配置，不能将某次测量当作普遍性能保证。

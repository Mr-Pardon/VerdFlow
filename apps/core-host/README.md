# verdflow-core-host

状态：Experimental  
架构分类：App / Composition Root  
维护责任：Core host maintainers

## 职责

为一个活动 VerdFlow 项目组装 `ProjectStore`、`ApplicationFacade`、`JobRuntime`、
`WorkerRegistry`、`ServiceLoop` 与已安装的 Worker Adapter。Host 持有项目写锁，
并保证 Service Loop、retention 和 object GC 在打开 ProjectStore 的所有者线程执行。
启动恢复会先调用每个 Worker Driver 的 `reconcile()`；各外部 Adapter 只收敛自己拥有的
`WorkerProcess`，随后 Job Runtime 才按 lease 处理 interrupted/retry。

## 公共入口

```python
from verdflow.core_host import CoreHost, CoreHostConfig
```

`CoreHost.create()` 与 `CoreHost.open()` 分别创建和打开项目；`add_source_file()`、
`create_identity_assembly()` 和 `inspect_scene()` 提供 Phase 1 最小纵向用例；调用方传入
已确认的精确规则包后，`extract_regions()` 通过注册的 Region Provider 执行提取并
定版不可变 RegionSet；当前组合根在启用 Blender Worker 时注册首个实现。调用方传入
已打开的只读 `StrategyRuntimeLibrary` 后，`generate_points()` 提供 Phase 2B 第一段
可信策略生成用例。检测到 Blender 后，Host 注册 Blender 高程 Provider，
`sample_elevation()` 通过统一请求生成不可变高程子 PointSet。Host 不创建、不关闭
或修改该用户库的生命周期。
产品侧策略 Worker 默认不设置总执行上限，`strategy_timeout_seconds=None`；自动化或
测试可显式设置有限正数，作为可选的子进程硬截止时间，而不是 Job lease。
同步 `generate_points()` 与 `PlantRunTimeouts.generation_seconds` 也默认无限等待，
避免 Worker 允许长跑但外层提前结束；其他阶段、网络与 SDK 等待预算保持不变。
生成期间持续续租并响应取消，不因面板离开或累计耗时较长而失败；监督心跳不等于
算法进度，卡住的策略由用户取消或显式执行上限终止。见 ADR-0003。
`assign_assets()` 使用项目冻结策略、自包含 Profile 或带当前 Catalog 来源的 runtime
override，并由 Core 内确定性 Worker 生成 AssignmentSet；
空间 PointSet 保持目标无关，同一点集可分别绑定 DTS、
Blender 或未来目标资产。
Host 始终注册轻量 DTS XML Worker；`export_output()` 只接受高程已解析 PointSet，
使用已冻结 AssignmentSet 并在最终输出时应用显式偏移，随后定版 ExportRecord。
`submit_composition_output_export()` 接受 ProjectStore 当前组合已经校验的有序批次清单，
复用同一 DTS Worker 生成单一 XML，并定版带全部输入谱系的 CompositionExportRecord。
`run_planting()` 是首个完整同步编排入口，按生成、高程、资产分配和输出四阶段执行；
每个阶段仍是独立持久 Job，相同精确请求重跑会复用已完成阶段，不创建通用 DAG。
项目维护默认启用：首个正常 service cycle 执行一次 bounded retention 和 object GC，
之后分别按 5 分钟与 24 小时周期运行；普通失败退避并通过
`maintenance_status()` 暴露最近结果，不终止 Worker 主链路。
不执行 Worker 的调用应设置 `enable_blender_worker=False`，避免只读或元数据命令
创建 Blender runtime。

模型 API Worker 通过 `enable_model_api_worker=True` 显式启用。Host 将 System 范围
Provider Registry 转换为 Core Provider Definition，并注册唯一命令所有者；密钥仍由
注入的 Secret Resolver 解析。`analyze_materials()` 在手动模式停在候选，在自动模式
经相同硬校验后定版精确规则包并提交 Region Extraction。`confirm_materials_and_extract()`
用于手动候选确认后的同步调用；`confirm_materials_and_submit_extraction()` 只确认并提交，
供产品传输层异步跟踪。分析与提取始终是两个独立持久 Job。
`submit_scene_preparation()` 将 Source Inspection 和持久 Material Flow 组合为产品级
异步入口；父 execution 只保存子 Job/Flow 引用，不重新实现 Blender 或模型逻辑。

`CoreHostExtensionFactory` 是组合根的受限扩展入口。Factory 只能使用 Host 创建的
`JobRuntime` 增量注册 Worker Driver、Region Extraction、Region Source 和 Elevation
Provider；不能替换
ProjectStore、ApplicationFacade、Plant Run 状态机或基础 Worker。DTS Live 等应用 Adapter
通过该入口接入，不要求 Core 感知 Explorer、WebUI 或相机。
`add_source_bytes()` 仅用于小型 Adapter 场景引用等受信任输入；
内置 `GeospatialExtensionFactory` 按需注册 SHP/GeoJSON Region Source 与 GeoTIFF
Elevation Provider，未挂载该扩展时不会启动或加载 GIS Worker；
`submit_extension_command()` 只接受已安装扩展 Driver 声明拥有的 command type，不能
成为上层提交任意命令的通道。

## 依赖边界

- 可以依赖 Core、ProjectStore 和具体 Adapter，作为唯一组合根。
- 可以组合共享 Worker Runtime，但不直接操作 PID；进程身份和协议恢复由具体 Adapter
  Supervisor 持有。
- 不实现几何、策略、SQLite repository 或 Blender 算法。
- CLI、Studio 和 MCP Bridge 只能调用 Host 公共入口。
- 当前 Store 与 Service Loop 保持单线程所有权，不跨线程共享 SQLite connection。
- Maintenance Scheduler 只做同线程到期判断，不拥有线程、进程或数据库连接。
- 用户 Library 由外层 SystemHost/CLI 组合持有；CoreHost 只消费授权和包定位端口。
- 扩展 Factory 只在 Registry seal 前执行；worker id、command type 和 provider id 冲突
  继续由既有注册表与 Provider 构造门禁拒绝。

## 验证

```powershell
python -m unittest tests.unit.test_core_host -v
python -m unittest tests.unit.test_material_flow tests.unit.test_model_api_worker_supervisor -v
python -m unittest tests.unit.test_planting_generation_stage -v
python -m unittest tests.unit.test_point_import tests.unit.test_blender_worker_supervisor -v
```

## 当前限制

- 首版一个 Host 只管理一个活动项目。
- 高程与区域提取用例均可按 Provider ID 路由；普通项目默认注册 Blender，DTS Live
  临时 Host 通过受限扩展注册 DTS Region/Elevation Worker。
- 材质候选
  已接入可选 API Worker 和持久手动/自动确认编排；Codex Agent Provider 已按 ADR-0009
  延期到多轮策略训练阶段。
- DTS XML 输出 Adapter 已接入；Blender 实例化输出 Adapter 尚未实现。
- HTTP/IPC 由外层 SystemHost/Local API 提供，CoreHost 自身不实现传输协议。
- 长任务执行期间没有并发查询入口；Phase 2 transport 必须将请求排队到 Core
  所有者线程，不能直接跨线程调用 ProjectStore。
- `create_identity_assembly()` 首版只提供单位矩阵便利入口；通用 Facade 已支持
  显式 `SourceAssemblyMemberSpec`。
- Maintenance 最近状态只在 Host 进程内存中保留；尚无跨重启历史和持久告警。
- `run_planting()` 同步兼容入口仍保留；正式异步流程由持久 Plant Run execution 推进。

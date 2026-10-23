# verdflow-system-host

状态：Experimental
架构分类：App / System Composition Root
维护责任：System host maintainers

## 职责

组合当前 OS 用户的 LibraryStore、机器级配置、SecretStore、最近项目导航和活动项目。
每个项目由独立 owner thread 驱动 CoreHost，query thread 读取已提交事实；长任务期间
查询和取消不排队等待 Worker 返回。Library 写操作在自己的所有者线程串行执行。

种植方案和手动配置复用同一 Plant Run，种植组合保存已确认批次引用，不拼接 XML。
DTS Live 创建不登记到最近项目的短时运行时，冻结本次选择并复用正式生成、分配和输出链。
策略示例是有界临时操作，不写成项目 PointSet；具体样本与角色说明仍由策略包持有。

## 公共入口

```python
from verdflow.system_host import SystemHost, SystemHostConfig
```

通过公开方法创建/打开/关闭项目、查询任务、提交种植、请求取消、管理库资源和机器环境。
跨 Catalog 的候选身份由 Planting 提供，Host 只组合来源查询与缩略图投影。
`profile_strategies.py` 统一解析已安装、已信任且通过结构校验的最新兼容策略，
遵循 [ADR-0016](../../docs/adr/0016-compatible-planting-plans.md)；纯查询不改写方案，
客户端不重复维护版本选择规则。
项目和库接口不向客户端暴露 SQLite 连接、对象路径或生成器源码。
细粒度请求与结果由公共 Contracts 定义，HTTP 映射见 Local API 模块。

## 依赖边界

- 可以组合 CoreHost、LibraryStore 和只读 Worker inspector，不实现 SQL、策略算法或 HTTP。
- Studio 的视窗查询和图形缓存属于独立 Studio Service，不进入业务编排。
- 机器路径与 Provider 配置不进入共享库或项目；API Key 通过 SecretStore 解析。
- DTS payload 转换属于 Adapter，Connector 拥有传输认证，WebUI 拥有相机与交互。
- 同一 system root 只允许一个 Host；endpoint 文件仅用于发现，不替代锁或认证。

## 验证

```powershell
python -m unittest tests.unit.test_system_host tests.unit.test_system_instance
python -m unittest tests.unit.test_project_runtime_channels tests.unit.test_local_api
python -m unittest tests.unit.test_system_model_config tests.unit.test_system_library_queries
python -m unittest tests.unit.test_plant_preview_product tests.unit.test_dts_live_vertical_runtime
```

完整环境说明见 [开发与测试](../../docs/development/TESTING.md)。

## 当前限制

- 活动会话存在于进程内；普通项目事实持久化，短时实时项目不承诺进程崩溃恢复。
- 项目显式关闭或 Host 退出时释放，当前没有项目闲置回收策略。
- 取消信号先即时投递，取消意图由 owner thread 持久化，两者之间有短暂进程内窗口。
- Provider 配置在项目打开时取快照；更换协议/模型配置后需重开项目，密钥则逐次解析。
- 平台真实回执、窗口与高程行为须在对应 Blender/DTS 环境单独验证。

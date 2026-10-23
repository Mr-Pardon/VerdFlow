# verdflow-dts-live-connector

状态：Experimental
架构分类：App / DTS Explorer Transport
维护责任：DTS live connector maintainers

## 职责

参数示例按 ADR-0010 通过有效会话窄接口调用 SystemHost。Connector 只维护预览句柄的
会话归属和回收，不执行算法、不透传桌面管理令牌。跨会话句柄被拒绝，关闭/晚到提交均取消。

为 SystemHost 持有的短时 DTS Live Session 提供 loopback HTTP 传输、一次性 bootstrap
交换、HttpOnly Session Cookie、精确同源检查、命令长轮询和类型化 Explorer 回调入口。

## 公共入口

```python
from verdflow.dts_live_connector import (
    DtsLiveConnectorApplication,
    DtsLiveConnectorConfig,
    DtsLiveConnectorServer,
    DtsLiveLifecycleService,
)
```

## 依赖边界

- 只把 HTTP DTO 转换为 `DtsLiveConnectorManager` 的类型化调用。
- 不读取策略、Catalog、Profile、ProjectStore 或 XML 文件，不执行种植算法。
- 不提供任意 DTS SDK 命令代理，不接受客户端指定任意 XML 路径。
- bootstrap nonce 和 access token 不写日志、项目或运行时文件。
- 选区成功后只调用注入的 `on_selection_ready` 排队端口；Session 关闭后调用
  `on_session_closed` 生命周期端口。Connector 自身不等待 Core 长任务。
- `POST /api/v1/operations/selection` 可接收 `regions` 集合，直接冻结配置并排队。
  Session 快照暴露 `selection_regions`，多面与单面均进入同一标准 RegionSet/Plant Run。
  选择/导出请求上限 32 MiB，导入草稿上限 30 MiB；轮询可用 `include_regions=false`
  省去轮廓序列化。文件 64 MiB/100000 顶点限制仍保留。
- `POST /api/v1/callbacks/elevation-chunk` 接受关联 ID、原点索引和最多 2000 个命中结果。
  最终 elevation 回调可携带 `chunked=true` 与失败面清单；服务端严格校验其余点完整性。
  Session `sampling_failures` 提供面 ID、名称及原因，详见 ADR-0007。
- `POST /api/v1/selections/export-shp` 通过注入的 `RegionExchangeService` 导出勾选面，
  由 Geospatial Adapter 编码二维 SHP ZIP，本机 Windows 另存为确认目的地并原子写入。
  不接受客户端路径，不创建 Core 任务，不修改选区或种植状态；取消保存不是错误。
- `POST /api/v1/selections/import-shp` 只接收空对象，经原生窗口选择 SHP/ZIP 后返回
  规范化选区集合；不接受客户端路径，也不修改当前运行。导入导出共享文件窗口互斥与
  最近目录记忆。几何解析及文件格式由 Geospatial Adapter 负责，详见 ADR-0007。
- `POST /api/v1/operations/regeneration` 只复用当前 Session 上一轮已验收的选区集合，
  重新冻结策略、Profile、参数和随机种子后进入既有生成链路；它不重新启动 Explorer
  绘制工具，也不绕过单操作并发门禁。
- `DtsLiveConnectorApplication` 是组合根：读取 SystemHost 已解析的 DTS SDK locator，
  启动同源 WebUI，并把配置查询与回调端口窄化注入 HTTP transport。
- WebUI 缩略图读取、Profile 详情读取与新 Profile 保存均由 SystemHost 端口注入；
  Connector 只校验会话、转换 HTTP DTO 和返回结果，不持有 LibraryStore 规则。
- `DtsLiveLifecycleService` 是显式按需控制器。构造与 `status()` 均无副作用；只有
  `start()` 才创建 Connector、bootstrap Session 和临时 CoreHost，`stop()` 负责清理。

## 验证

```powershell
python -m unittest tests.unit.test_dts_live_connector_server tests.unit.test_dts_live_lifecycle -v
```

## 当前限制

- Explorer WebUI 静态页面与 SDK 调用已接入；真实 Explorer 纵向门禁尚未执行。
- Session 仍是进程内短时状态，重启不恢复。
- 已接入多面草稿提交、空间窗口采样、单面采样失败隔离及 SHP/ZIP 导入导出；
  不含洞、视图加载检测、几何合并和执行分批。多面基础种植已获用户真实验证，
  ADR-0007 的大范围优化尚待真实 DTS 验收。

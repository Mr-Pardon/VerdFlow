# verdflow-local-api

ADR-0023 增加受管理鉴权保护的 `GET /api/v1/resource-center`、
`GET /api/v1/resource-center/detail?id=...&version=...` 和
`GET /api/v1/resource-center/strategy-updates`。目录与更新查询可传 `refresh=1`。
现有资源预检支持 `selection: { resource_id, version, revision }`，与 paths/recommended
互斥；仍不接受客户端下载 URL。预览不触发下载、导入或策略信任。

ADR-0021：地理输入错误由 SystemHostDataError 保留 code/message，经本传输返回 400；
异步数据错误通过既有 JobErrorInfo 暴露，不在 HTTP 层解析 SHP 或猜测错误原因。

ADR-0020 增加管理鉴权的 `POST /api/v1/resource-imports/prepare` 与
`POST /api/v1/resource-imports/confirm`，查询/取消复用 `starter-resources/import`。
Local API 只校验有界请求并转发 SystemHost，不下载或解析交换包。详见
[资源导入契约](../../docs/architecture/RESOURCE_IMPORTS.md)。

状态：Experimental  
架构分类：App / Local Transport  
维护责任：Local API maintainers

## 职责

把 loopback HTTP 请求转换为 `SystemHost` 公共调用。当前暴露健康检查、项目生命周期、
最近项目发现、Job 列表/监控与取消、项目业务摘要、本地 Source 导入、Source Assembly、
Scene Preparation、材质分析/候选确认、Region Source/提取、
可选 Studio Service 的 Region Viewer 与 Point Review 分块查询、Plant Run 异步提交/查询/Review 确认、Artifact 元数据和模型 Provider 管理，不保存业务状态，也不
直接访问 SQLite、SecretStore、CoreHost 或 Worker。

## 内部结构

- `server.py`：HTTP 路由、认证、错误映射、流式响应与监听生命周期。
- `resource_paths.py`：版本化资源路径识别与 ID 解码。
- `request_values.py`：请求字段、查询参数和基本值约束。
- `request_documents.py`：传输文档到 SystemHost 类型化请求的纯转换。
- `__main__.py`：独立服务启动与便携版父进程退出监听。Windows 监听继承管道时只读取
  已有数据，不在空 stdin 上阻塞，避免后续首次加载 NumPy/OpenBLAS 原生库时挂起；
  管道 EOF 仍请求正常关闭，监听器不持有或关闭外部传入的句柄。

`resource_paths.py`、`request_values.py` 与 `request_documents.py` 不持有 Host、HTTP 请求
对象、线程或存储，不执行用例。实际业务决策仍由
SystemHost/Core 持有；删除 Profile 只操作当前独立 Profile，不保留历史版本谱系别名。
响应前对未读取的范围内请求体做有限等待清理，避免 Windows 关闭连接时丢失正常错误
响应；不解析未授权业务，也不无界消耗超限请求。

## 公共入口

推荐资源接口均要求管理认证：

- `GET /api/v1/starter-resources`：按需读取已审核清单，返回 revision 和资源列表。
- `POST /api/v1/starter-resources/import`：提交 `{ revision }`，立即返回 202 和 operation_id。
- `GET /api/v1/starter-resources/import?operation_id=...`：查询逐项下载/导入结果。
- `DELETE /api/v1/starter-resources/import`：提交 `{ operation_id }` 请求取消。

只接受宿主持有清单的 revision，不接受客户端提供任意 URL、路径或包描述。
状态与后台线程属于 SystemHost，不属于 HTTP Handler；清单读取不自动安装或信任。

```python
from verdflow.local_api import LocalApiConfig, LocalApiServer
```

当前端点包括：

- `GET /api/v1/health`、`GET /api/v1/projects`
- `GET /api/v1/recent-projects`
- `POST /api/v1/projects`、`DELETE /api/v1/projects/{session_id}`
- `GET /api/v1/projects/{session_id}/summary`
- `GET /api/v1/projects/{session_id}/tasks?status=...&before=...&limit=...`
- `GET /api/v1/projects/{session_id}/region-sets/{region_set_id}/viewer`
- `GET /api/v1/projects/{session_id}/region-sets/{region_set_id}/geometry?min_x=...&min_y=...&max_x=...&max_y=...`
- `GET /api/v1/projects/{session_id}/region-sets/{region_set_id}/overview?min_x=...&min_y=...&max_x=...&max_y=...`
- `GET /api/v1/projects/{session_id}/region-sets/{region_set_id}/selection?corner=x,y`
- `GET /api/v1/projects/{session_id}/point-sets/{point_set_id}/review?assignment_set_id=...`
- `GET /api/v1/projects/{session_id}/point-sets/{point_set_id}/points?assignment_set_id=...&min_x=...&min_y=...&max_x=...&max_y=...`
- `GET /api/v1/projects/{session_id}/jobs/{job_id}`
- `GET /api/v1/projects/{session_id}/jobs/{job_id}/events`
- `GET /api/v1/projects/{session_id}/jobs/{job_id}/attempts`
- `POST /api/v1/projects/{session_id}/jobs/{job_id}/cancel`
- `GET /api/v1/projects/{session_id}/artifacts/{artifact_id}`
- `POST /api/v1/projects/{session_id}/sources`
- `POST /api/v1/projects/{session_id}/source-intake`：Studio 创建项目后提交后台源文件登记。
- `POST /api/v1/projects/{session_id}/source-assemblies`
- `POST /api/v1/projects/{session_id}/scene-preparations`
- `GET /api/v1/projects/{session_id}/scene-preparations/{execution_id}`
- `POST /api/v1/projects/{session_id}/scene-preparations/{execution_id}/confirm`
- `POST /api/v1/projects/{session_id}/scene-preparations/{execution_id}/cancel`
- `POST /api/v1/projects/{session_id}/material-analyses`
- `GET /api/v1/projects/{session_id}/material-analyses/{job_id}/candidate`
- `POST /api/v1/projects/{session_id}/material-analyses/{job_id}/confirm`
- `POST /api/v1/projects/{session_id}/region-extractions`
- `POST /api/v1/projects/{session_id}/region-extractions/{job_id}/finalize`
- `POST /api/v1/projects/{session_id}/region-sources`
- `POST /api/v1/projects/{session_id}/region-sources/{job_id}/finalize`
- `POST /api/v1/projects/{session_id}/material-flows`
- `GET /api/v1/projects/{session_id}/material-flows/{execution_id}`
- `POST /api/v1/projects/{session_id}/material-flows/{execution_id}/confirm`
- `POST /api/v1/projects/{session_id}/material-flows/{execution_id}/cancel`
- `POST /api/v1/projects/{session_id}/plant-runs`
- `GET /api/v1/projects/{session_id}/plant-runs/{execution_id}`
- `POST /api/v1/projects/{session_id}/plant-runs/{execution_id}/cancel`
- `POST /api/v1/projects/{session_id}/plant-previews`
- `GET /api/v1/projects/{session_id}/plant-previews/{execution_id}`
- `DELETE /api/v1/projects/{session_id}/plant-previews/{execution_id}`
- `POST /api/v1/projects/{session_id}/plant-previews/{execution_id}/commit`
- `POST /api/v1/projects/{session_id}/plant-previews/{execution_id}/export`
- `GET /api/v1/projects/{session_id}/plant-previews/{execution_id}/export`
- `POST /api/v1/projects/{session_id}/plant-previews/{execution_id}/re-export`
- `GET /api/v1/projects/{session_id}/plant-previews/{execution_id}/re-export?export_job_id=...`
- `POST /api/v1/projects/{session_id}/plant-exports`
- `GET /api/v1/projects/{session_id}/plant-exports/{execution_id}`
- `GET /api/v1/projects/{session_id}/planting-composition`
- `DELETE /api/v1/projects/{session_id}/planting-batches/{batch_id}`
- `POST /api/v1/projects/{session_id}/planting-composition-exports`
- `GET /api/v1/projects/{session_id}/planting-composition-exports/{composition_id}?export_job_id=...`
- `GET /api/v1/projects/{session_id}/exports/{export_id}`
- `GET /api/v1/projects/{session_id}/exports/{export_id}/content`
- `GET /api/v1/model-providers`
- `PUT/DELETE /api/v1/model-providers/{provider_id}`
- `PUT/DELETE /api/v1/model-providers/{provider_id}/secret`
- `PUT /api/v1/model-providers/{provider_id}/model`
- `POST /api/v1/model-providers/{provider_id}/models`
- `POST /api/v1/model-providers/{provider_id}/test`
- `GET /api/v1/strategies`、`GET /api/v1/strategies/{package_hash}`
- `POST /api/v1/strategies/import`
- `POST /api/v1/strategy-packages/import`
- `POST /api/v1/strategies/{package_hash}/export-package`
- `POST /api/v1/strategies/{package_hash}/trust|revoke`
- `DELETE /api/v1/strategies/{package_hash}`
- `GET /api/v1/asset-catalogs?target_type=...`
- `GET /api/v1/asset-catalogs/{target_type}/{catalog_id}`
- `GET /api/v1/asset-catalogs/{target_type}/{catalog_id}/assets`，支持
  `collection_id`、`query` 与稳定 ordinal 分页
- `PUT/DELETE /api/v1/asset-catalogs/{target_type}/{catalog_id}`
- `POST /api/v1/asset-catalog-packages/import`
- `POST /api/v1/asset-catalogs/{target_type}/{catalog_id}/export-package`
- `POST /api/v1/library-resources/import`
- `GET /api/v1/library-resources/{resource_id}`
- `GET /api/v1/role-binding-profiles?target_type=...`
- `GET /api/v1/role-binding-profiles/{profile_id}`
- `POST /api/v1/role-binding-profiles`
- `DELETE /api/v1/role-binding-profiles/{profile_id}`
- `POST /api/v1/role-binding-profile-packages/import`
- `POST /api/v1/role-binding-profiles/{profile_id}/export-package`
- `POST /api/v1/authoring-kits/{skill_id}/export`
- `GET /api/v1/external-runtimes`
- `PUT/DELETE /api/v1/external-runtimes/{runtime_id}`
- `GET /api/v1/dts-live/status`
- `POST /api/v1/dts-live/start|relaunch|stop`

Provider、secret、外部运行环境、Catalog 写入/删除、资产/策略/Profile 包交换、创作工具包导出、本地路径 Source 导入和各类取消操作要求管理 bearer token。独立 CLI 只从
`VERDFLOW_LOCAL_API_MANAGEMENT_TOKEN` 环境变量读取该令牌，不接受命令行明文参数；
未配置时写操作保持关闭。桌面 Shell 可在构造 `LocalApiConfig` 时直接注入随机令牌。

Plant Run 取消返回 `accepted` 和 `worker_signal_sent`，只表达请求受理与信号投递；
execution 是已提交状态的读取快照，可能仍为 running。客户端继续查询至取消中或终态，
不得因 202 响应直接解锁，也不得要求取消命令在传输线程同步等待 Worker 退出。

服务监听成功后向 `system-config/system-endpoint.json` 原子发布地址、协议版本和稳定
进程身份，关闭时按 `instance_id + endpoint_id` 精确清理。独立 CLI 再次启动时会验证
健康响应与 endpoint 身份；匹配则输出 `reused: true` 并退出，不创建第二个 SystemHost。

Region Viewer 的 `geometry` 按 `next_after_ordinal` 续读。区域项的 `boundary_mode`
区分常规视窗细节、预算内简化轮廓和仅包围框三种显示方式（`detail`、`overview`、
`extent`）。首次详情请求可用 `overview_threshold` 获取 `overview_recommended`，
从而跳过超密集视窗的详情请求。只读 `overview` 支持同样的类型筛选和视窗边界，
返回行优先、从北向南的格网（`pixels` 是 zlib 压缩并以 Base64 编码的 uint32
小端序数组）。0 为透明，其他值是 `items` 中从 1 开始的下标；条目为
`[region_id, classification, footprint_area_xy]`，只用于显示。
只读 `selection` 用原始边界处理一个 `corner=x,y` 的点击或重复四次的
旋转框选，支持 `classification` 和 `after_ordinal`，返回 `region_ids` 和下一游标。
它不保存选择，客户端应在最后一页返回后再一次性应用。

## 依赖边界

- 通用业务请求 DTO 从 `verdflow-system-host` 公共门面取得；Viewer DTO 来自独立的
  `verdflow-studio-service`。本包不直接依赖 ProjectStore、LibraryStore、CoreHost 或 Adapter。
- Local API 进程是桌面端最外层组合入口，可以控制独立 `dts-live-connector` App 的
  生命周期，但不得实现 DTS 协议、策略或种植逻辑。创建 Local API 与读取状态均不启动
  Fast Path；只有受管理令牌保护的显式 `POST /dts-live/start` 可以启动。
- 只允许绑定 loopback 地址，首版不提供远程网络服务。
- HTTP handler 可以并发，但所有项目操作由 SystemHost 路由到项目 owner thread。
- Source Assembly 与 Region Source 端点只负责形成规范 `SceneRevision + RegionSet`；后续种植
  必须通过既有 Plant Run 端点，不允许传输层直接调用生成器。
- 不在传输层实现种植、策略、资产映射或错误恢复规则。
- HTTP 响应只暴露 `secret_configured`，不得返回 API Key 或 secret value。

## 验证

```powershell
python -m unittest tests.unit.test_local_api tests.unit.test_system_instance -v
python -m unittest tests.unit.test_system_model_config -v
python -m unittest tests.unit.test_system_library_queries -v
python -m unittest tests.unit.test_plant_preview_product -v
```

## 当前限制

- 当前只有受信任本地路径导入，没有浏览器 multipart/分块上传和任意 Artifact 内容下载；
  正式 Export 已有按 ExportRecord 解析的受控下载流。大文件导入仍同步复制并计算内容
  hash；若 Studio 实测出现超时，再独立设计可观察的 Source Ingestion，不由 Scene
  Preparation 暗中承担。
- 低层 `material-analyses` 与 `region-extractions` 仍保留给调试和高级组合；完整流程应使用
  `material-flows`。自动模式由持久协调器推进，人工模式显式确认后恢复。
- Job Event 当前使用增量补读，不提供 SSE/WebSocket 推送。
- Viewer 接口由按需组合的 Studio Service 返回产品 DTO，不进入 SystemHost 或 Core，
  也不暴露 Artifact 路径。Region geometry 单页最多 5,000 个，
  Point 单页最多 20,000 个，均使用稳定 ordinal 续页；Point 还支持 Role 与 Asset 筛选。
- 每个活动项目的 query thread 首次访问 Region/Point/Assignment Artifact 时会构建有界
  LRU 内存投影；后续视窗查询复用投影。当前 Artifact 仍是整体 JSON，因此首次解析成本
  与完整数据量相关；只有出现真实超大样本证据后才升级持久空间索引或流式 Artifact。
- 项目任务使用 `(created_at, job_id)` 不透明游标分页；业务摘要仅覆盖当前打开项目，关闭
  项目的卡片只显示 Registry 可用性，打开后再取得精确业务状态。
- Catalog 资产使用稳定 ordinal 分页且强制指定 `target_type`；当前 canonical Catalog
  仍是单个 SQLite BLOB，因此每个无缓存请求会解析完整当前文档。真实大库出现性能证据
  后再评估 Library 资源索引或 Studio Service 的可丢弃只读缓存。
- Plant Run 取消会返回请求是否接受以及当前 Worker control signal 是否已发送；最终
  execution/Job 状态仍需继续查询。取消意图在 owner thread 写入前存在极短进程内窗口。
- `plant-previews` 是对同一 Plant Run 状态机的产品级约束，不是第二套种植流程。预览提交
  会拒绝输出字段；DELETE 只放弃当前未提交且停在 Review 的预览；导出动作默认零偏移，
  并由 SystemHost 根据项目 lineage 解析 Provider。
- 种植组合端点只登记或删除已确认批次引用，并提交/查询一个事实重渲染的组合输出 Job；
  commit 可透传可选 `replacing_batch_id` 完成已保存批次的原子替换。Local API 不做 Region
  冲突判断、点位合并或 XML 拼接。
- 已完成预览的 `re-export` 只按新偏移提交独立 Output Export Job；GET 端点补读并收尾该
  Job，不回退或重开 Plant Run，也不重新执行点位生成与高程采样。
- `plant-exports` 接收与预览相同的显式种植选择并直接自动续接高程和输出；它拒绝客户端
  指定 Review policy 或底层 Provider，不实现 Region、策略或 Profile 自动选择。
- 管理令牌目前保护 Provider/secret 写操作、本地路径导入和取消；现有项目生命周期端点仍没有
  客户端认证。
- 不发送宽松 CORS 头；Studio 应由同源服务托管或使用桌面端受控请求层。
- HTTP 请求状态不是持久业务事实，重启后由 ProjectStore 恢复项目数据。
- DTS Live 事件当前保留在有界进程内队列，服务重启不恢复；Studio 只在用户显式启动后
  补读状态。Explorer 启动参数由桌面 PlatformBridge 限定为已配置可执行文件和 loopback
  `-webui` URL。

## Multi-Catalog Search

`POST /api/v1/asset-search` requires the existing management authorization. The JSON request contains `target_type`, `provider_id`, `catalog_ids`, optional `query`, `collection_catalog_id` + `collection_id`, `offset`, `limit` (1-200), and `revision`. The response contains `items`, source-qualified `collections`, `total_count`, `offset`, `next_offset`, `revision`, and `unavailable_catalogs`. It never mutates catalog or plan state. A revision change resets pagination to the first page.
## 矢量区域准备

Studio 项目入口使用管理授权的 `POST /api/v1/projects/:session/source-intake`。请求包含
`workflow`（`fbx`、`vector` 或 `imagery`）、`source_path`、`idempotency_key`；矢量及影像
还需 `elevation_path`，FBX 可附材质选择、确认方式和模型服务。接受后返回 `202` 与 Job；
进度通过现有 Job 和项目摘要读取。这个 Job 负责登记完整输入并启动原有模型检查、矢量
区域准备或影像识别。`POST .../source-intake/:job/retry` 从相同文件位置重新登记失败或取消
的任务，可复用未变化的已复制对象。取消沿用 Job 取消接口。项目命令只保存本机输入令牌，
外部路径不写入项目。已有 `/sources`、`/source-assemblies` 及各单独准备接口保持不变。

工作台矢量入口调用 `POST /api/v1/projects/:session/region-sources` 时可附
`auto_publish: true`（需要管理授权）。返回 202 和已有 Job 标识；计算、Core 校验和
激活由项目宿主完成，不再要求客户端补发 `/finalize`。项目 summary 的
`vector_preparation` 表示尚未定版的运行、失败或取消。省略该字段保留显式定版流程；
影像候选不接受自动定版。

## 影像输入试用

`GET /api/v1/health` 的 `imagery_available` 表示本地模型与 Worker 解释器已配置。
既有 `POST /api/v1/projects/:session/region-sources` 在 `provider_id` 为
`verdflow.geospatial.imagery` 时接受 Assembly `source_id`，以及 `options` 中的
`imagery_source_id`、`elevation_source_id`，以及可选的非负整数 `min_region_pixels`
（旧调用兼容，默认为 0；Studio 新任务不提交），不接收模型路径或后端切换参数。
`GET /api/v1/projects/:session/region-sources/:job/imagery-preview?page=1` 分批读取多环区域，
返回总候选数、批次数和旧任务的剔除统计。仅第一页包含有限大小的底图 PNG。新任务还返回
全图候选序号 RGB PNG（0 为背景，RGB 从低到高编码整数）和一基顺序的 `review_index`；
两张图共用尺寸及仿射变换。旧任务以已有分页边界生成显示用概览，不重新执行识别。
Studio 在预览请求附 `compact_index=1`。新任务超过 32,768 项时第一页可返回
`review_index_encoding: "ordinal-f64-u32-le/1"`，此时 `review_index` 为空。通过
`GET .../imagery-review-index?start=0` 按零基位置续读 `start`、`count` 和 `rows_base64`，
每次最多 32,768 行。每行小端 `Float64` 原图像素数和 `Uint32` 一基边界批次；区域 ID
由一基序号生成（`region_00001`），与全图 RGB 概览和候选顺序一致。普通规模和旧任务
仍走原接口，传输分块不需要用户翻页。
第一页另附 `source_grid`（原影像尺寸、仿射和 RGB 波段）。授权后可通过同一 Job 下的
`GET .../imagery-tile?level=0&column=0&row=0` 读取视野细节：原影像像素网格上每块
512 像素，`level` 为 2 的降采样指数，图幅边缘块可较小。响应包含底图 PNG、仿射变换，
以及带 1 像素边缘缓冲的 RGB 候选序号 PNG，序号按返回的 `region_ids` 从 1 索引。
大型候选首次建立临时索引时，可返回 `{"retry_after_ms":350}`，客户端保留概览并
在视窗仍需要该瓦片时自动重试。无法读取时可返回 `null`，不重试当前帧。请求只接受
Job 和瓦片索引，不接收文件路径。
`POST .../:job/finalize` 接受 `included_region_ids`，或互斥的 `excluded_region_ids`（空列表表示全保留）。
使用分块索引的任务也可 `POST .../:job/finalize?selection=bitmap`，提交 `included_region_bits`
及可选 `expected_region_set_id`。位图按零基候选顺序从字节低位到高位排列，Base64 编码，
末尾补位必须为零。此授权请求仍有限制：默认最多 3 MiB，足以覆盖 PNG 的 24 位编号范围；
不提高其他请求的体积预算。重新打开时 `confirmed_region_bits` 使用相同编码。
Studio 的影像确认另带 `background=1`，返回 `202` 和已持久化的 Job（`job_id`、`status` 等）。
该选项只用于已完成的影像候选，必须有管理授权。`GET .../projects/:session/jobs/:job_id`
读取状态；预览第一页以 `pending_confirmation_job_id` 暴露仍在执行的确认。离开页面只停止读取；
任务成功与区域版本切换一起提交，失败或取消仍保留原候选与旧版本。无 `background` 的原同步返回保持不变。
提交、预览及带选择的确认要求现有管理授权。已确认的影像项目在 `summary.imagery_job_id`
保留识别入口；预览第一页返回当前 `confirmed_region_set_id`、`confirmed_region_ids` 和可选的
`selection_locked_reason`。再次提交时携带 `expected_region_set_id`，按原候选派生新的区域集，
不覆盖旧版本或重跑模型；陈旧请求、现有种植批次或未结束的种植任务会阻止变更。未确认任务在项目
`summary` 显示 `imagery_preparation`，完成后进入 `awaiting_region_confirmation`。

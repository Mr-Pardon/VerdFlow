# verdflow-local-api

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
- `GET /api/v1/projects/{session_id}/point-sets/{point_set_id}/review?assignment_set_id=...`
- `GET /api/v1/projects/{session_id}/point-sets/{point_set_id}/points?assignment_set_id=...&min_x=...&min_y=...&max_x=...&max_y=...`
- `GET /api/v1/projects/{session_id}/jobs/{job_id}`
- `GET /api/v1/projects/{session_id}/jobs/{job_id}/events`
- `GET /api/v1/projects/{session_id}/jobs/{job_id}/attempts`
- `POST /api/v1/projects/{session_id}/jobs/{job_id}/cancel`
- `GET /api/v1/projects/{session_id}/artifacts/{artifact_id}`
- `POST /api/v1/projects/{session_id}/sources`
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

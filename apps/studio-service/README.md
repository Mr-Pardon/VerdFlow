# verdflow-studio-service

状态：Experimental  
架构分类：App / Studio Read Service  
维护责任：Studio service maintainers

## 职责

为 VerdFlow Studio 提供可丢弃的只读界面投影。当前负责 Region Viewer 与 Point
Review 的 bbox 裁剪、稳定分块、Role/Asset 图例、临时空间索引和有界 LRU 缓存。
Asset 图例名称来自该 Assignment 冻结的 `TargetAssetUseSnapshot`，不回查可变资产库；
稳定资产 ID 继续作为颜色、筛选和输出引用。
它不保存项目事实，不提交 Job，也不实现绿地、种植、高程或导出规则。

## 公共入口

```python
from verdflow.studio_service import StudioQueryService
```

`StudioQueryService` 按项目 key 延迟打开只读 `ProjectQueryStore`。只有第一次 Viewer
查询才创建项目查询线程和解析 Artifact；`close_project()` 与 `close()` 释放连接和全部
临时缓存。

点位索引与植被分配表分别最多保留 16 个 Artifact、合计 500,000 行，按 LRU 淘汰。
行数是缓存成本估算，不是严格的内存字节上限；单份超过预算的投影仍单独保留，避免
同一大批次翻页时反复解析。已保存 bbox 与视窗不相交的批次在解析 Artifact 前直接返回
空块。排队查询等待超时后取消未开始的 Future；已经运行的只读查询可完成并预热缓存。

## 依赖边界

- 可以依赖 Contracts 与只读 ProjectQueryStore。
- 不依赖 Core、CoreHost、SystemHost、Worker、DTS 或 Blender。
- 不写 SQLite、Artifact、LibraryStore 或用户项目目录。
- Renderer 不直接依赖本包；它通过 Local API 消费 DTO。
- CoreHost、SystemHost 与 CLI 不依赖本包，纯终端执行不会创建 Studio 查询线程。

## 验证

```powershell
python -m unittest tests.unit.test_project_viewer_queries tests.unit.test_studio_query_runtime -v
```

## 当前限制

- v1 Artifact 是整体 JSON，首次查询仍需完整解析；后续视窗请求复用临时投影。
- 缓存按 Artifact 数量和点位行数限制，不是持久空间索引；真实超大场景达到证据门槛后再评估
  流式 Artifact 或磁盘派生索引。
- 查询按项目单线程串行，Renderer 应合并连续视窗变化并丢弃过期响应。

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

点位不超过 100,000 行且两个 Artifact 合计不超过 32 MiB 时使用内存索引；
每类内存索引最多保留 8 个、合计 250,000 行。更大的批次逐行建立项目 `cache/`
中的可丢弃 SQLite 投影。索引采用完整坐标和原始序号，只有全部行通过检查后才可查询；
按 LRU 保留最多 8 个、合计 768 MiB（可保留一个更大的当前索引，仍检查磁盘余量）。
图例单独流式读取映射，不等待空间索引。视窗不相交时不打开 Artifact；每次点位响应
最多 5,000 行和约 8 MiB，沿用现有游标且不裁掉正式点位。排队查询等待超时后取消
未开始的 Future，运行中的查询可完成并预热缓存；关闭项目查询时中止索引构建并清理。

区域几何不超过 32 MiB 时保留内存快路径。更大的 Artifact 按区域读取精确边界并建立
可丢弃的 SQLite 查询索引，不在内存保留全部顶点。视窗每次响应最多 5,000 项、
60,000 个顶点和约 8 MiB；单区域超过 20,000 个顶点时降级显示为保留洞的轮廓，
无法安全简化时仅显示包围框。画幅超预算或存在降级边界时，点击、框选使用原始几何，
而不是从简化的画布图形推断，正式数据不变。

区域选择与点位预览共用密集视窗概览。详情不足以覆盖当前画幅时，用所有相交区域的
原始边界分批生成最长边 1,024 像素的栅格；普通画幅不生成栅格。格网只记录像素能
代表的区域，单个像素重叠时不承担几何选择语义。首次大几何索引可以同时产出概览；
会话只缓存最近两个画幅和筛选组合，关闭时清理。

## 依赖边界

- 可以依赖 Contracts、Planting 的 Artifact 读取器与只读 ProjectQueryStore。
- 不依赖 Core、CoreHost、SystemHost、Worker、DTS 或 Blender。
- 不写 `project.db`、正式 Artifact 或 LibraryStore；只写可丢弃的项目 Viewer 缓存。
- Renderer 不直接依赖本包；它通过 Local API 消费 DTO。
- CoreHost、SystemHost 与 CLI 不依赖本包，纯终端执行不会创建 Studio 查询线程。

## 验证

```powershell
python -m unittest tests.unit.test_project_viewer_queries tests.unit.test_studio_query_runtime -v
```

## 当前限制

- 大点集首次视窗查询需扫描两个 Artifact 建索引；后续请求复用索引。
- 大区域集首次视窗查询仍要扫描源几何建索引。极密集视窗只显示概览像素分辨率可表达
  的覆盖；选择仍按完整边界查询，不受格网分辨率影响。
- 查询按项目单线程串行，Renderer 应合并连续视窗变化并丢弃过期响应。

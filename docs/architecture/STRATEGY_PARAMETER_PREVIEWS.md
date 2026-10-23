# 策略参数示例

依据：ADR-0010 / ADR-0010。示例与静态用途示意分开，前者执行原生成器，后者只解释角色关系。
实时入口与共享展示归属参见 ADR-0010。

## 策略声明

```yaml
extensions:
  parameter_preview:
    version: '1'
    display_name: 道路绿带
    fixed_seed: 17
    boundary_xy: [[0, -3], [160, -3], [160, 3], [0, 3]]
```

一个策略只提供一个无内洞边界。坐标是本地米制 XY，生成时 Z=0，固定 Region ID 为
`strategy_example`。名称最多 80 字，种子为 0 至 2147483647 的整数，顶点 3 至 256 个，
坐标绝对值不超过 10000，单轴跨度不超过 1000 米，XY 面积 0.01 至 50000 平方米。
边界应简单、非自交，弧形由有限折线近似。不要通过缩小米制尺度假装修改点位间距。

生成器收到精确策略文档、默认公开参数与本次覆盖、包内固定种子、标准 Region 和
`excluded_role_keys`。输出仍是原 PointSet v2，再由 Adapter 后过滤并投影。应用不得
生成替代点位、按策略 ID 选择算法，或将资产参数隐式解释成策略参数。

## 用例与 Local API v1

- `POST /api/v1/strategy-previews`：管理权限与精确包信任均必需。输入只有 `package_hash`、
  可选 `strategy_parameters` 数值对象、可选 `excluded_role_keys` 数组。未声明参数、越界值、
  非有限数、未知角色、重复角色或全部关闭均拒绝。无用户可控种子、地块、路径或脚本。
- 返回 202 的 `StrategyPreviewOperation`，含 `preview_id`、`package_hash`、`status`、可空
  `result`、可空 `error_message`。状态为 pending/running/succeeded/failed/cancelled。
- `GET /api/v1/strategy-previews/{preview_id}`：只读内存状态；成功结果 schema 为
  `verdflow.strategy-example-preview`，version 为 `1`，含精确 `package_hash`、`role_keys`
  与 `points: [[x, y, role_index], ...]`、逐项对应的稳定 `point_ids`。允许空数组，不是可持久化或可导出的 PointSet。
  缺少可用稳定 ID 或 ID 超过临时解析预算时 `point_ids` 为空，只退回普通符号，不改变生成契约。
- `DELETE /api/v1/strategy-previews/{preview_id}`：管理权限；幂等取消并释放保存的结果。
- 已结束结果保留最多 4 份、最长 180 秒；过期资源返回 400。最多 2 个并发、8 个活动请求。
  超限请求、示例预算超限与执行错误都不改变正式项目任务。

请求、操作、输入与结果值对象位于 Contracts；可选声明校验在 Planting；授权和参数归一化
在 Core；进程、包校验和投影在 Strategy Worker；内存调度在 SystemHost；UI 请求通过统一
LocalApiClient。宿主级临时目录在 system-root/runtime/strategy-previews，不创建项目事实。

## UI

参数变动延迟合并后提交，旧操作取消。包含提交响应晚到、轮询晚到和组件卸载的 revision
守卫。更新期间保留原图并标记，包变化立即清空旧图。错误可重试，不阻止正式生成。
样本种子固定不受真实种植种子影响；真实种植种子仍可在高级设置中调整。
画布按实际米制比例绘制，角色色彩沿用用途示意。Studio 可将当前搭配的参考半径乘以解析
缩放叠加为透明范围圆，不代表真实冠幅或碰撞边界；缺失尺寸保留原有符号，范围不沿地块裁剪。
Profile 既有候选优先使用其保存的尺寸快照，新加候选才读取 Catalog，沿用显式同步规则。
本次工作台会话保留最后完成的示例；编辑搭配后回来不重跑策略。半径只影响绘制，候选、
权重和缩放只影响临时搭配解析；公开算法参数或角色开关才改变生成输入。
图例可切换对应角色点位的可见性，包含参考范围和中心点；右上角的“实心点位”滑动开关
隐藏范围圆并将中心点改为不透明符号。这两项仅是组件内的显示状态，不改变角色启用、
Profile 或输出选择，也不发起生成或搭配解析请求。默认仍为原有参考范围呈现。

## 临时搭配解析

`POST /api/v1/asset-assignment-previews` 必须有管理权限，输入只有：

```json
{"random_seed":17,"points":[["stable-point-id","role"]],"roles":[{"role_key":"role","candidates":[{"asset_id":"asset","weight":1,"scale":{"mode":"fixed","value":1}}]}]}
```

缩放也支持 `uniform_random` 的 `min`/`max`。响应为与输入点序对应的
`choices: [[candidate_index, scale], ...]`；未映射角色返回 `[-1, 1]`，显示退回角色点。
最多 100000 点、128 角色、每角色 100 候选，ID 最多 256 字、角色键 128 字；权重/缩放
必须有限正数，缩放最多 1000000，种子遵循正式 Resolver 的 0 至 2^63-1 约束。
该路由请求体上限为常规配置的 8 倍（默认 8 MiB），不放宽其他路由。未知字段均拒绝。
它复用正式 Resolver 的哈希选择和随机缩放，不接收半径、坐标、Profile ID 或目标 locator，
不查询库或创建运行事实。该请求与策略请求、生成缓存完全分离；返回晚到时由 Studio 丢弃。

## 实时 WebUI

`@verdflow/strategy-preview-ui` 共用通用画布、控件与异步状态；应用注入自己的 client，
不从 WebUI 引用 Studio 私有文件。深色附属侧板由 WebUI 持有，默认关闭，仅打开时请求。
共享组件通过 `theme` 提供明暗配色；WebUI 使用深色画布、网格、边界和提亮后的角色色，
Studio 默认保持浅色。主题不进入请求或缓存签名，不触发策略/搭配解析，不改变点位和范围。

Connector 在已有同源与 HttpOnly 会话 cookie 下提供相同的 `/api/v1/strategy-previews`
提交、`/{id}` GET/DELETE 和 `/api/v1/asset-assignment-previews` POST。不传递桌面管理令牌。
每个句柄严格绑定 Connector 会话，最多四个未释放句柄；其他会话和桌面句柄不可访问。
`POST /api/v1/strategy-previews/cancel-all`（空对象）仅取消当前会话的示例，页面退出时
使用 keepalive；不会取消种植。会话/Connector 关闭也回收所有对应操作，包括晚到提交。
策略预览 DTO 与搭配解析 DTO 的解码函数由 Contracts 公共入口共享，传输仍只处理文档。

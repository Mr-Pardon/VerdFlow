# verdflow-dts-live-webui

ADR-0016：方案选择采用服务端解析的兼容策略 hash；同时安装多个版本时按解析结果
去重，手动配置仍可使用服务端提供的适用方案。旧方案未配置的新增角色默认为关闭。

状态：Experimental
架构分类：App / DTS Explorer WebUI
维护责任：DTS live experience maintainers

## 职责

面板使用与 Studio 共用的 `assets/branding/` 素材，深色面板采用浅色字标，收起后使用
独立方形图标。Vite 将资源复制到 Connector 静态目录，不从用户库或外部网络读取 Logo。

提供嵌入 DTS Explorer 的轻量右侧操作面板。页面只通过同源 Connector API 读取可执行
策略、DTS Catalog/Profile，并执行多面草稿绘制、空间窗口采样和统一 XML 回载。一级面板
承载策略、Profile、策略公开的构图参数、运行状态与选区入口；角色映射等低频配置进入
独立工作区。

ADR-0005：搭配工作区可跨库连续选择，切库不清空候选；来源限定 ID 和完整目标由服务
提供，已选区缓存来源展示。仅本次应用提交完整草稿，不创建临时库或方案；卸载清理缓存
和过期资产查询。来源库移除后，名称/尺寸/执行目标仍取已保存快照，图片允许占位回退。

## 公共入口

- `src/main.ts`：Explorer Renderer 启动入口。
- `src/api.ts`：同源 Connector API client。
- `src/sdk/DtsSdkBridge.ts`：DTS SDK 生命周期、选区、高程与导入命令适配。

CEF 模式下，适配层在全局回调和 SDK 实例入口定向消费 `ImportXMLObject` 响应，兼容
未转义的 Windows 路径；请求路径使用正斜杠。广播事件仅接受完整 XML 路径匹配的真实回执，
不把派发确认或无路径广播当作成功；当前 SDK 调用自身的明确业务结果也可作为回执，
无需再等第二份带路径事件。迟到 Promise 只属于原调用，不能完成新任务。30 秒只提示耗时较长，
继续等候回执；服务端默认也不以五分钟到期判失败。
取消释放本页等待，但不能停止已进入 DTS 的原生导入，应检查场景后再重试。
WebUI 生命周期内保护 `document.write` / `document.writeln`，避免 SDK 诊断覆盖页面。
销毁时取消待处理采样/导入，恢复自己包装的回调和文档方法；若已有较新的接管者则不覆盖它。
迟到事件和命令不再执行，不把待处理操作泄漏给重新打开的面板。

## 内部结构

| 目录/模块 | 职责 |
|---|---|
| `App.vue` | 组合各控制器、共享锁定状态和页面布局，不持有轮询、存储或 SDK 调用 |
| `components/` | 策略、搭配、参数、选区及映射工作区视图，只消费类型化控制器 |
| `features/useLiveSession.ts` | 会话恢复、命令轮询、阶段提示、启动/取消与 SDK 生命周期 |
| `features/useLiveConfiguration.ts` | 配置同步、策略/Profile 选择与参数/映射控制器组装 |
| `features/useRoleMapping.ts` | 本次映射副本、Collection/搜索、权重/缩放及显式 Profile 保存 |
| `features/useAssetGridPagination.ts` | 按实际网格列数补齐整行分页，配对清理尺寸监听 |
| `features/useStrategyParameters.ts` | 参数和种子草稿、默认值恢复与合法性检查 |
| `features/useRegionDrafts.ts` | 选区列表、分页/定位、文件交换、清空确认与预览清理 |
| `features/useFloatingPanel.ts` | 拖动、折叠、视窗约束及成对的浏览器监听 |
| `features/useExamplePanel.ts` | 示例侧板开关、方案侧板互斥、当前搭配事实与短暂缓存 |
| `features/useAdjacentPanel.ts` | 两个附属侧板的定位、移动通知合并和浏览器资源释放 |
| `sdk/protocol.ts` | SDK 本地类型，外部协议不进入控制器或 Core |
| `sdk/regions.ts`、`sdk/elevation.ts` | 选面解析、空间窗口规划及高程回包归一化 |
| `sdk/importResponses.ts` | XML 回执的兼容解析，不参与策略或 Profile 解释 |
| `sdk/xmlImport.ts` | XML 回执等待、完整路径匹配、慢任务提示及原生回调/文档方法保护 |
| `sdk/camera.ts` | 相机返回值的有限数值解析；采样视角生命周期由 SDK Bridge 持有 |
| `shared/presentation.ts` | 本应用的纯显示/草稿辅助函数，不持有状态或服务生命周期 |
| `shared/panelScroll.ts` | 面板局部滚轮滚动，兼容宿主取消原生滚动，不向场景穿透 |
| `styles/` | 按面板职责拆分样式；入口保留原有级联顺序 |

拖动控制器按动画帧合并鼠标位置，只保留最新一次输入；开始拖动时读取尺寸，移动时只更新
`transform`，不逐个事件读取布局或修改 `left/top`。松手、失焦与销毁时清理待处理帧，
保留全局捕获监听作为 CEF pointer capture 的回退。主面板不使用背景模糊，降低嵌入式
浏览器透明合成的开销；配色与交互布局不因此改变。

不通过全局可变单例或组件互相调用私有方法共享状态。控制器拥有自己的监听、轮询与
销毁标记，根组件只用明确回调连接会话与选区；角色名称、示意与参数语义仍由策略包持有。
严格类型检查同时启用未使用变量/参数检查；结构测试检查直接 transport/SDK 越层导入、
模块环与未接入入口的源码，不能只把旧根组件原样移进另一个大文件。

## 依赖边界

- 不直接访问 LibraryStore、ProjectStore 或文件系统。
- 选区工具栏一行放置绘制、导入 SHP、导出 SHP，下面展示统一选区列表。
  新绘制选区追加并默认勾选，保留旧面的勾选和显示状态。种植完成后取消成功面的勾选并
  隐藏其预览；采样失败面保留，方便重试。眼睛按钮统一切换全部分页和搜索范围外的选区预览，不改变勾选；
  批量显隐按小块让出 UI，期间锁定选区和种植提交，失败项保留原显示状态并提示。
  “导入 SHP”由 Connector 原生窗口选择 SHP/ZIP，成功后追加为勾选草稿并显示贴地预览。
  “导出 SHP”仅提交当前勾选草稿，由 Connector 弹出原生另存为窗口并写出 ZIP。
  不要求策略或 Profile，不触发高程采样；导出或取消均保留列表与场景预览。
- 临时角色映射可仅用于本次种植，也可通过 Connector 的窄命令交由 SystemHost 保存为
  自包含 Profile；WebUI 不自行生成 Profile 标识或写入存储。
- 从已有 Profile 切换为本次手动映射时，先通过 Connector 读取其自包含 assignments，
  再以副本进入编辑；Catalog Collection 仅用于筛选候选资产，不改变映射数据。
- 搭配工作区按用途导航、植被浏览和已选配置分区；浏览与配置独立滚动，搜索/分类不随
  植被列表滚走。大资产库以 48 项为基准，按实际列数向上补齐整行，末页仅显示剩余资产；
  窗口尺寸变化时保留原页首资产所在页，搜索或切换分类/用途回到第一页。缩略图按需加载，失败时回退默认占位。
  窄屏以“选择植被 / 已选植被”切换两个工作区，不将权重和缩放挤到列表下方。
- 底部明确区分“仅本次应用”与“保存植被搭配并应用”。保存按钮先打开同风格确认窗口，
  从已有搭配进入时可选择更新或另存，没有已有搭配时只新建；返回调整保留当前草稿。
  名称、可选参数和更新目标在确认窗口内呈现，只有显式确认才写入 Profile。
- 同一角色选择多个资产时，界面以整数百分比编辑，并在新增、删除或修改权重后自动配平
  到合计 `100`；提交时仍转换为合计 `1` 的权重。该规则只负责降低编辑成本，Core 仍
  接受任意正权重并按相对比例解析。
- 植被缩略图通过受会话授权保护的 Connector 资源端点读取，不复制到 Live Session。
- 植被用途卡片、分组和静态构图示意与 Studio 共用 `@verdflow/role-guide-ui`，资料来自
  SystemHost 只读投影；它不解释具体策略，不改变 Profile 或角色的执行语义。
- DTS SDK 路径由 Studio/SystemHost 配置，WebUI 只加载 Connector 暴露的固定 SDK 文件。
- 主面板延续深墨绿和薄荷绿配色；策略列表固定高度、版本独立成行，参数默认折叠。
  可拖动头部和底部操作区固定，选区列表和配置内容滚动，映射弹窗沿用 Studio 浅色风格。
- 图标来自应用内有限的 Lucide 通用语义集合，不允许策略包注入 SVG、组件或脚本。
- 当前操作开始后锁定策略、映射、随机种子与构图参数，完成、失败或取消后恢复调整。
- 空闲时每 5 秒重新读取一次 SystemHost 配置选项，页头也提供手动同步入口。刷新会保留仍
  存在的策略、Profile 与本次手动映射；Studio 新导入并信任的策略或新导入的 Profile 无需
  重启 Connector 即可出现在 Explorer 面板。

## 开发

参数示例通过 `@verdflow/strategy-preview-ui` 共用展示，客户端请求注入 Connector 会话 API，
不依赖 Studio 私有源码。仅侧板打开时生成；关闭或收起主面板会释放监听/帧/轮询，取消
后台示例并保留最近完成的单份结果。页面退出补发 keepalive 会话级取消（ADR-0010）。
图例和实心开关仅绘制；搭配/缩放不重跑策略。深色外壳与主面板一致，不重复参数控件。

```powershell
corepack pnpm --filter @verdflow/dts-live-webui typecheck
corepack pnpm --filter @verdflow/dts-live-webui test
corepack pnpm --filter @verdflow/dts-live-webui build
```

构建产物写入 `apps/dts-live-connector/src/verdflow/dts_live_connector/static`。

## 验证

```powershell
corepack pnpm --filter @verdflow/dts-live-webui typecheck
corepack pnpm --filter @verdflow/dts-live-webui build
python -m unittest tests.unit.test_dts_live_connector_server -v
```

## 当前限制

- 连续绘制只创建贴地预览，点击“开始种植”才提交勾选面；一批共用一套配置。
  成功后清理成功面预览但保留轮廓；采样失败面单独列出，可仅勾选失败面重新种植。
  相机按 1024 米空间网格定位与短等待，不检测地形加载完成，不合并/切分原始几何。
  网格仅组织采样点，相机按窗口内实际点位的 XY 范围和射线中点高度定位，不把小选区
  放大为整块 1024 米网格，也不使用射线的上下端点撑高视角。边界仍留 15%/至少 2 米余量。
  贴地选区预览的边框取 bbox 短边的 0.4%，限制在 0.01 至 1 米；不修改选区几何。
  每次采样前仅记录一次当前相机，通过同一 SDK 回调索引兼容 CEF 和 Promise 模式；
  成功、失败、取消或面板销毁时恢复原位置与朝向（与定位一致的 0.8 秒飞行）。
  正常收尾等待飞行时长再执行后续导入，避免原生导入阻塞打断相机动画；取消/销毁释放定位等待计时器。
  读相机失败会明确提示但不阻断采样；仅保存当前任务视角，不进入 Core、策略包或项目事实。见 ADR-0007。
  采样块按冻结点索引回传，单面重试后仍失败时继续其他面；不是持久化断点续传。
  DTS 单射线回包可能直接携带 `LineIntersectPoint` 和 `result: 0`，仅在请求数为 1
  时归一化为一行；多点仍严格校验返回数量，空交点或无效坐标仍按采样失败处理。
  大列表每页 50 面，支持搜索及点击本页面 Polygon 自动定位，详见 ADR-0007。
  SDK 就绪后显式设置 `settings.setMousePickMask(1)` 启用左键拾取；仅注册 `onEvent`
  不会开启场景拾取。未开启移动/悬停拾取，避免大量无关事件。
  贴地 Polygon 的点击可能返回底下的 `TileLayer`；此时用 `MouseClickPoint` 的 XY
  命中可见选区，先筛 bbox 再用 Canvas Path2D 精确判定轮廓（坐标减去 bbox 原点）。
  路径按需缓存、隐藏时释放，重叠时优先最后显示的选区；不改勾选或生成几何。
  贴地模式需 DTS 场景已开启对象贴合；此版真实效果由用户测试。
- Profile 的导入、导出、删除与完整管理继续由 Studio 承担；Live WebUI 只允许从当前
  有效映射创建或更新 Profile。
  保存时可同时包含当前种植参数；选择带预设的 Profile 时恢复默认值加预设，之后仍可临时
  调整。纯搭配不改变参数草稿，随机种子不进入 Profile（ADR-0006）。
  参数面板依据策略 `visible_for_roles` 和当前 Profile/临时映射的角色开关显示控件，
  不推断具体策略键，不删除已隐藏参数的草稿或保存值。
- 真实 Explorer SDK、选面、高程和 XML 回载仍需完成主线现场门禁。

## Multi-Catalog Browsing

The mapping workspace uses `@verdflow/asset-browser-ui` with the authenticated connector route `POST /api/v1/configuration/asset-search`. The feature controller supplies transport; views do not call the SDK or transport directly. Search scope never enters assignments or triggers strategy execution. Unmount aborts browser fetches and clears debounce, observers and listeners; completed selections remain self-contained.

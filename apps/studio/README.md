# @verdflow/studio

方案文件导入与新手推荐资源共用 `StarterResourcesDialog` / `useResourceImport`：先由
SystemHost 预检查，展示策略补齐、独立的可选来源资产库和显式覆盖选项，再提交确认。
可选库及覆盖默认不选；选择本地 VFS 会重新预检查。关闭弹窗停止轮询并取消未完成操作，
迟到的受理结果也会被取消。导入完成只提供前往策略库的入口，不自动信任或生成预览。
来源缺失不影响自包含方案执行；同 major 兼容判断与发现索引均不在 Renderer 重复实现。

ADR-0016：种植方案消费 SystemHost 的 `resolved_strategy_hash/version`，默认使用
已信任的兼容新版；详情同时展示保存基线与当前版本，不在 Renderer 推断兼容规则。

设置页提供应用更新，顶部仅在发现更新或下载/准备时显示轻量入口。AppShell 持有单个可选
PlatformBridge 订阅，卸载时解除；浏览器开发环境不执行更新。来源、下载、校验和替换归桌面壳，
不通过 Core 或业务 API 执行。见 [ADR-0015](../../docs/adr/0015-portable-application-updates.md)。

状态：Experimental  
架构分类：App / Vue Renderer  
维护责任：Studio UI maintainers

## 职责

顶部品牌和 favicon 使用根目录 `assets/branding/` 的共享应用资源，版本从 workspace
`package.json` 读取，不在界面写死。Vite 开发与正式构建使用同一静态资源入口，支持
桌面 `file://` 相对路径；这些资源不属于 Core 或策略。
左上角 Logo 链接到 `https://mr-pardon.github.io/verdflow-site/` 用户手册；浏览器使用新标签，
桌面壳通过精确地址白名单交给系统浏览器，不替换当前工作页面。

首次引导位于 `src/features/onboarding/`，样式位于 `styles/features/onboarding.css`。
资产与策略步骤提供“导入推荐资源”：`StarterResourcesDialog` 展示在线清单，
`useStarterResources` 通过 Local API 提交、查询和取消，结束后刷新当前页面。
仅用户打开弹窗时联网，不在 Renderer 下载文件、解析包或决定信任；失败保留手动导入入口。
资源中心地址交给桌面壳限定页面白名单打开，详情链接可访问配套 PAK 的说明。
空配置首次启动提供欢迎页，已有配置不自动打断；设置中的“产品引导”可以继续或重看。
页面通过可释放的注册提供真实状态及既有动作；引导不复制导入、信任、环境配置或种植业务，
不依赖尚未准备的随包资源。VFA Author / VFS Author 为可选 Agent 创作工具包，不是直接
导入的资源。浏览记录与就绪状态分离，本机偏好不保存文件路径、密钥或资源内容。
详见 [ADR-0017](../../docs/adr/0017-first-use-guide.md)。

Renderer 样式从 `src/styles/base.css` 的有序入口加载，功能样式位于 `src/styles/features/`；
按完整规则和现有级联拆分，不把页面状态或业务逻辑放进样式层。严格类型检查同时拒绝
未使用变量和参数，避免旧组件/图标导入长期保留。

Blender 工作台与资产页签展示一致的“规划中”状态，当前不开放 Blender 场景种植或资产编辑。
入口和提示由 Studio 管理，不改变 DTS 的 FBX 几何处理能力。方案详情通过现有库查询展示
已解析策略和各个来源资产库的名称；来源缺失使用可读提示，不让可选名称读取阻塞方案使用。

提供 VerdFlow Studio 的 Vue 3 + TypeScript Renderer。当前纵向切片包含全局壳层、
SystemHost 连接状态、最近项目页、DTS 首页、FBX 与地理项目创建对话框，以及模型处理、任务状态和
材质确认、Region Viewer、区域选择、种植配置工作台、策略库和资产库，以真实 Local API
DTO 展示项目 Registry、业务摘要、执行事实和 User Library 资源。
本包不读取 SQLite、Artifact、策略包或项目对象目录，也不实现绿地、种植、高程和输出
算法。

ADR-0005：工作台和种植方案编辑器切换资产库仅切换浏览区，保留跨库选择、权重和缩放。
候选身份由服务提供，原始 ID 和来源名称只用于展示；完整草稿可直接提交，不要求保存方案。

种植配置通过通用 `StrategyParameterEditor` 展示策略声明的数值参数与随机种子。
控件只编辑本次运行草稿，用户显式重新生成后才提交 `generation_args.strategy_parameters`；
重新打开已有预览时恢复实际提交值。界面不识别特定策略参数键，也不把参数保存到 Profile。

`RoleMappingEditor` 以“植被用途”卡片组织角色，通过 `@verdflow/role-guide-ui` 展示
SystemHost 返回的可选分组、推荐和静态构图示意；资料缺失时保留原有展示。推荐不是素材
限制，角色标识、权重、缩放与开关仍按既有配置提交。

## 公共入口

- `src/main.ts`：Renderer 启动入口。
- `src/platform/index.ts`：当前运行环境的 PlatformBridge 门面。
- `src/api/index.ts`：Local API client 与响应类型入口。
- `src/router/index.ts`：产品稳定路由，不保存 Project Session ID。

## PlatformBridge

所有本机能力只通过 `src/platform/bridge.ts` 中的 `PlatformBridge` 进入 Renderer：

- 获取或复用 Local API 连接。
- 选择 Source 文件和项目目录。
- 选择策略包目录。
- 选择导出副本位置。
- 在资源管理器中显示路径。
- 以专用 IPC 启动已配置的 DTS Explorer，并注入受控 loopback `-webui` URL。

开发态 `WebDevBridge` 只提供同源 Local API 连接。它明确返回不支持本地文件、目录、
另存和资源管理器操作，不使用浏览器上传或下载语义模拟桌面能力。
`apps/studio-electron-probe` 已实现同一接口的 Electron 探针；Vue 组件仍不得导入壳层
SDK，后续 Tauri 对照也必须复用该接口。

## 目录

```text
src/
  api/            Local API client 和版本化响应类型
  components/     全局壳层与通用组件
  features/       按项目、DTS 等产品能力组织页面、用例状态和展示模型
  platform/       可替换 PlatformBridge
  styles/         全局 token 与布局基线
```

API DTO 保留后端 snake_case 字段，避免建立一套无收益的序列化映射。业务状态到中文文案
的转换只存在于 feature presentation model，不产生新的 Core 状态。

DTS 工作台只在当前 execution 处于活动状态且页面可见时做有界状态补读；进入等待确认或
终态后停止。任务是否卡住、失败或可重试仍由 SystemHost/Job Runtime 给出，Renderer 不按
心跳时间自行派生业务结论。材质确认提交完整的三类精确分类，AI 置信度与证据只用于预选
和展示。

Region Viewer 通过 Local API 使用 `verdflow-studio-service` 的 manifest 与视口分块 DTO。
`RegionCanvas.vue` 只负责世界坐标到画布的稳定变换、绘制和输入；多环面命中与框选相交
由独立 `regionGeometry.ts` 处理；`useRegionViewer.ts` 负责过期请求丢弃、稳定续页和临时
选择集合。选择在提交 Plant Run 前不写入项目事实。
交互约定为左键点选/框选、中键平移、右键旋转和滚轮缩放；旋转只改变观察变换，不修改
Region 世界坐标或提交数据。

Point Review 使用相同只读视窗边界，远景只展示区域概览，放大后读取与视窗相交的批次
点位。参考半径参与视窗外扩，单点或线状点集仍可显示；未种植区域保留为空间参考。
`usePointReview.ts` 同时只推进一轮视窗加载，连续变化仅保留最新待加载视窗，并停止
旧视窗的后续分页。离开页面丢弃待加载视窗和迟到结果，不修改已保存点位或输出选择。
连接失败提供可读提示与当前视窗重试；重新读取清单失败时不覆盖原错误。

种植配置由 `PlantingStage.vue` 展示，`usePlantingConfiguration.ts` 只编排 Library 查询与
Plant Preview 提交。引导依次展示种植方式、植被搭配和可选效果设置；已有搭配可直接生成，
手动搭配使用逐角色选择和默认收起的分配、缩放设置。`usePlantingChoicePreviews.ts` 只读取
卡片所需的说明、名称与缩略图；预览信息缺失不阻塞自包含 Profile 执行。用户必须显式选择
受信任策略和植被搭配，只有手动选择植被时才需要资产目录；工作台同时列出未信任
策略，并允许用户在选择时通过二次确认信任当前精确包哈希。RoleBindingProfile 是
可选模板，也可按 Role 建立只用于本次运行的完整 override。Renderer 不猜测资产映射，
提交后复用既有 Plant Run 人工 Review 门禁。提交响应立即同步到工作台，execution 的有界
补读统一由 `useDtsWorkspace.ts` 负责，不随种植子面板卸载停止。切换阶段或重新进入项目时，
配置锁定跟随持久任务状态；排队、运行和取消中不可重复生成，等待预览确认或终态后解锁。
短暂的提交请求期间暂停流程切换与页面离开，避免请求已被接收但 UI 丢失提交结果。
项目重入优先恢复 execution 状态与输入，再读取材质详情；配置尚未恢复时提供独立等待
状态，任务与取消入口不依赖该读取完成。相同 execution 的不可变输入只读取一次，失败
仍可重试；新 execution 重新读取。种植配置就绪后才挂载编辑器，后到的材质状态不覆盖
已恢复的种植阶段或用户当前页签。
工作台常驻 `PlantingTaskStatus.vue` 显示当前任务、累计耗时与取消入口，切换阶段后仍可
取消；耗时从持久 execution 的 `created_at` 计算，重新进入项目不会重置。运行超过
5 分钟仅给出非阻断提示，不修改后端状态。取消复用既有 Plant Run API，取消请求期间
防止重复发送，后台仍在取消时不解锁；请求失败保留原执行状态并允许重试。
取消接口受理后，即使返回快照仍为 running，当前页面也保持“正在取消”并防止重复请求；
这是界面等待状态，不伪造持久 execution 状态。补读取消中或终态后释放该本地等待标记，
新 execution 不继承旧标记；旧请求迟到失败不覆盖已经结束或替换的任务。
用户可以把当前 Review 保存为项目当前组合中的一个批次并返回区域选择；已经提交的精确
Region ID 在 Canvas 中保持可见但不可再次选择，后续 Review 会叠加历史批次点位作为构图
上下文。点位预览按批次折叠展示资产图例，允许为本次输出进一步关闭整批或单个资产，
Renderer 不重写已保存 PointSet。若关闭当前未提交批次但仍保留此前批次，Studio 会先调用
SystemHost 放弃当前预览，再继续输出既有选择。
批次登记后主状态即为“已保存”，不会隐式启动高程或单批 XML。只有用户进入输出页并点击
“生成 XML”后，Studio 才提交一项统一组合输出操作；首次输出按需采样各批高程，随后只生成
一份合并 XML。
已保存批次可从点位预览直接进入重新种植。Studio 恢复该批次提交的 Region、精确策略包、
Profile/Catalog 引用、本次映射覆盖、参数和随机种子；旧批次在新预览确认前保持不变，确认时通过 SystemHost 原子替换并保留原
批次序号。取消配置或生成失败不会提前删除旧批次。
删除已保存批次需经二次确认，并释放对应 Region。
输出页在首次高程采样和 XML 完成后仍允许修改坐标偏移，并通过独立 re-export 操作复用
既有高程点位与资产映射。该交互不会重新生成 PointSet，也不在 Renderer 内伪造新的
Plant Run 状态。页面重建时从 Local API 查询该预览最近一次正式 ExportRecord，不依赖
Renderer 临时状态，也不退回首次导出的偏移。
点位预览页以“输出预览点位”统一提交单批或多批组合输出。组合页只传递偏移并展示统一高程
与 XML 状态；多输入谱系校验和单一 XML 渲染由 Core/SystemHost/DTS Adapter 承担，不在
界面聚合数据。成功 XML 以项目文件列表独立留档，支持重复另存和显式删除；切换页面或继续
种植不会清除历史文件。

地理项目创建入口接收一个 SHP 或 GeoJSON 区域源和一个 GeoTIFF 高程源。Shapefile 伴随文件
由 SystemHost 在应用边界封装，Renderer 只提交类型化 Source；Region Source 定版后使用统一
业务摘要中的 SceneRevision 与 importer 谱系进入区域选择、植被种植、点位预览和输出四阶段
工作台，不伪造 FBX 材质流程。地理项目仍调用既有 Plant Run、Profile、Review 和 DTS XML
输出用例，页面不直接调用 Worker 或生成器。

策略库只管理精确策略包版本、详情、信任和卸载；资产库按 target 浏览当前 Catalog 与分页
资产，并通过独立编辑器管理资产、Collection、建议缩放和受管缩略图。独立植被搭配页浏览
策略 Role 与自包含 Profile。Catalog 保存直接更新当前内容，内部 generation 只用于防止并发
覆盖，不展示为业务版本。三页不读取策略源码、SQLite 或 Library 内部路径；资产库通过
PlatformBridge 选择 `.vfa` 路径，标准包导入或导出当前 Catalog 的不可变内容快照，不在
本地建立版本树。植被搭配页支持新建、编辑、删除 Profile，并通过独立 `.vfp` 标准包导入
导出；`.vfp` 不复制对应策略包或资产资源，也不要求 Catalog 存在。项目工作台可以加载
Profile 或建立本次临时映射；Catalog 漂移只作为非阻塞提示。
Catalog 和 Profile 均提供二次确认删除；项目运行快照已冻结实际执行事实，不参与删除门禁。
ADR-0004：Profile 和临时映射支持角色启用开关，关闭保留候选数据。Review 图例按资产
选择保留点位，输出选择草稿独立于 Profile；回到输出时携带选择和偏移，只重导出 XML。
选择改变后不能保存旧文件。`useOutputAssetSelection` 负责界面草稿，执行事实仍由服务持有。

## 依赖边界

- 可以依赖 Vue、图标库和公开 Local API 契约。
- 不依赖任何 Python package、SQLite、ProjectStore、CoreHost 或 Worker。
- 本机路径能力只调用 PlatformBridge；组件不得导入具体桌面壳 SDK。
- Viewer 派生计算由 `verdflow-studio-service` 完成，Renderer 只渲染 DTO。

## 开发

在仓库根目录运行：

```powershell
corepack pnpm install
corepack pnpm dev
corepack pnpm dev:desktop
corepack pnpm typecheck
corepack pnpm build
```

`dev:desktop` 是 Studio 联调入口，会自动启动 Local API/SystemHost、Vite 和 Electron；
单独的 `dev` 只启动浏览器侧 Vite 服务。两者都不会启动 DTS Live Connector、临时
CoreHost 或 Explorer；这些资源只在 `/dts/realtime` 点击“启动实时种植”后按需创建。

Vite 默认把 `/api` 代理到 `http://127.0.0.1:8768`。可以通过
`VITE_VERDFLOW_API_TARGET` 修改开发代理目标；`VITE_VERDFLOW_API_BASE_URL` 只用于
受控请求层已有明确地址的场景。前端环境变量不得保存管理 token 或 API Key。

## 验证

```powershell
corepack pnpm typecheck
corepack pnpm build
python -m unittest tests.unit.test_repository_governance -v
```

## 当前限制

- 当前已实现项目首屏、DTS FBX 与地理项目创建、模型处理状态、项目任务摘要、材质确认、Region
  Viewer、区域选择、种植配置、Point Review、输出操作、策略库和资产库纵向切片；独立
  任务中心仍未接入。DTS 实时种植页已提供显式启停与结构化生命周期日志，真实 Explorer
  单区域纵向门禁已经通过；异常退出后的临时目录 TTL 清理仍待验证。
- WebDev Bridge 不能创建项目、选择 FBX、另存 XML 或打开资源管理器。
- Electron 探针已经提供本地文件、目录、另存、资源管理器和 v0.1.0 便携 sidecar 能力，
  但不代表最终桌面壳选型。
- FBX Source 摄入需要 Local API 管理令牌；桌面主进程和 Local API 必须由同一启动器注入
  同一个令牌。开发模式仍由统一启动脚本显式注入；便携内测模式由 Electron 生成随机令牌
  并托管包内 Core sidecar。
- 全局连接状态仍通过显式刷新获取；当前 DTS 工作台只对活动 execution 做可见性受控的
  增量补读，JobEvent 时间线与任务详情留待独立任务中心。
- TypeScript DTO 当前由契约测试人工保持一致，schema 生成器尚未引入。
- 植被搭配支持可选种植参数预设（ADR-0006）：带预设时加载策略默认值加预设，纯搭配不改
  参数草稿；显式保存可包含参数，随机种子不保存。旧批次仍按冻结执行输入恢复。
- 参数展示根据策略包 `visible_for_roles` 与角色开关过滤，隐藏不清除值；无条件声明的参数
  始终显示。工作台在搭配后通过主按钮进入效果阶段，底部统一提供保存搭配与生成预览。
  效果参数默认展开，高级设置默认收起。
- 地理项目当前属于开发验证入口：真实业务 SHP/GeoJSON + GeoTIFF 的坐标与视觉门禁尚未
  完成；若应用在 Region Source Job 完成后、定版前异常退出，当前也不会自动恢复定版操作。

## Multi-Catalog Browsing

Workbench role selection and the planting-plan editor share `@verdflow/asset-browser-ui`. Catalog checkboxes define only search scope; saved candidates, weights and scales survive scope changes. Source labels and source-qualified categories distinguish catalogs. The SystemHost query pages across the entire scope, rather than fetching complete catalog contents. Local IPC responses are ignored after view disposal.

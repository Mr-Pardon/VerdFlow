# verdflow-strategy-preview-ui

状态：Experimental
架构分类：App / Shared Presentation
维护责任：Studio 与 Explorer WebUI 展示维护者

## 职责

为两个 Renderer 提供通用参数示例组件、Canvas 绘制和短暂请求生命周期。

## 公共入口

公共入口为 `@verdflow/strategy-preview-ui`。类型、组件和 composable 均从此导出。
样本/角色来自策略声明，资产范围由应用提供的搭配尺寸与通用分配结果组合。
实例卸载会取消生成、清除轮询/防抖/绘制帧并释放 ResizeObserver，迟到提交会补发取消。
调用方可提供单会话 `StrategyExampleCache` 复用最后完成的结果；不得跨用户/会话共享。

组件提供通用 `theme="light" | "dark"`，默认浅色。画布底色、网格、边界、比例尺、参考圈
及图例统一切换主题；深色模式仅提升角色颜色的亮度，不改变角色含义、策略执行或缓存。
壳样式通过 `--color-*` 变量适配。侧板位置、授权、打开/关闭及选项来源
由各应用持有。`header-actions` 插槽仅供宿主放关闭按钮，不允许策略注入组件或脚本。

## 依赖边界

- 只依赖 Vue、通用角色展示和现有图标库。
- 通过 `StrategyPreviewClient` 注入应用 transport；不依赖 Studio PlatformBridge、Connector、
  SDK、Core 或文件系统，不持有策略算法、样本、目标资产解析或持久化规则。
- 明暗主题只改变呈现，不影响请求、点位、搭配、角色状态或缓存签名。

## 验证

运行 Studio/WebUI 单元测试、类型检查和构建；浏览器回归两种主题的非空画布、参数更新、
搭配重绘、图例/实心模式和关闭后的资源清理。

## 当前限制

- 只展示策略包声明的固定样本，不代表项目选区或最终种植结果。
- 参考范围并非真实冠幅或碰撞边界；缺少半径时沿用通用点位符号。
- 页面位置、授权和长期资源管理必须由宿主应用负责。

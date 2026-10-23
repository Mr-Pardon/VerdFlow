# verdflow-asset-browser-ui

架构分类：App / Shared Presentation
维护责任：Studio 与 Explorer WebUI 展示维护者

## 职责

为 Studio 与 WebUI 提供多资产库范围选择、统一检索、来源限定分类和整行分页展示。
查询防抖、迟到响应丢弃、AbortController、ResizeObserver 和外部点击监听由组件生命周期清理。

## 公共入口

`@verdflow/asset-browser-ui` 导出 `AssetBrowser` 和通用展示类型。宿主传入兼容库元数据、
分页查询回调、已选身份及可选身份投影，通过缩略图插槽呈现图片，消费植被选择事件。

## 依赖边界

- 只依赖 Vue 与通用图标，不直接访问 Core、LibraryStore、SDK 或应用私有模块。
- SystemHost 持有查询与候选身份，宿主注入 transport；组件不实现资产解析或持久化。
- 检索范围按目标与 Provider 保存为本地偏好，不成为执行输入；取消勾选库不删除已选植被。
- 分类保持来源限定，跨库同名/同 ID 不合并；末页不填充虚假条目。

## 验证

Studio 的资产浏览测试覆盖查询合并、范围变更、身份、过期响应、分页和清理；WebUI
架构测试约束传输依赖。两个 Renderer 均需通过类型检查、构建和不同宽度的浏览器回归。

## 当前限制

- 只合并相同目标及兼容 Provider 的浏览范围，不扩展执行兼容性或放宽冲突检查。
- 资产搭配、权重、缩放和运行快照仍由宿主应用与领域层负责。

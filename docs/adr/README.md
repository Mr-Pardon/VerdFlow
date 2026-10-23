# Architecture Decision Records

这些记录解释 VerdFlow 的模块职责、数据契约和设计取舍。状态为 Accepted 的决定是实现
和评审依据；改变既有决定时应补充新记录和替代关系，而不是抹去旧决定的适用范围。

| 编号 | 决策 |
|---|---|
| [0001](0001-module-and-application-boundaries.md) | 模块所有权与应用边界 |
| [0002](0002-project-facts-and-coordinates.md) | 项目事实、不可变对象与坐标 |
| [0003](0003-hosts-jobs-and-workers.md) | 宿主、耐久任务与外部 Worker |
| [0004](0004-strategy-contract-and-trust.md) | 策略契约、角色与显式信任 |
| [0005](0005-catalogs-bindings-and-snapshots.md) | 资产目录、自包含搭配与运行快照 |
| [0006](0006-planting-plans-and-execution.md) | 种植方案与统一执行主链 |
| [0007](0007-live-sampling-and-delivery.md) | 实时多面采样与场景交付 |
| [0008](0008-geometry-and-geospatial-providers.md) | 几何与已对齐地理输入 |
| [0009](0009-structured-model-providers.md) | 结构化模型与本机密钥 |
| [0010](0010-strategy-presentation-and-examples.md) | 策略展示与参数示例 |
| [0011](0011-package-exchange-and-authoring.md) | 包交换与 Author 工具包 |
| [0012](0012-desktop-shell-and-user-data.md) | 桌面壳与稳定用户数据目录 |
| [0013](0013-open-source-and-third-party-boundaries.md) | 开源许可与第三方边界 |
| [0014](0014-example-and-sensitive-data.md) | 示例与敏感数据 |
| [0015](0015-portable-application-updates.md) | Windows 免安装应用更新 |
| [0016](0016-compatible-planting-plans.md) | 策略语义版本与方案兼容 |
| [0017](0017-first-use-guide.md) | 首次使用引导与本机进度 |
| [0018](0018-curated-starter-resources.md) | 推荐资源清单与快捷导入 |
| [0019](0019-multi-catalog-browsing.md) | 多资产库检索与独立配置快照 |

## 阅读原则

- 产品可用性以对应版本的说明和验证结果为准；架构预留的入口不等于已交付集成。
- 具体策略的构图规则在策略包内，平台协议在适配器内，展示交互在应用层。
- 示例中的标识和数据应为合成内容，不指向真实客户工程或开发者机器。
- 设计中预留的扩展端口不等于已经实现所有集成，支持范围以对应模块的说明为准。

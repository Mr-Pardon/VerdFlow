# VerdFlow Packages

这里是 VerdFlow workspace 模块的发现入口，包括 Python distribution、共享前端组件
和外部系统 adapter。架构归属和依赖方向以
[模块边界与分类](../docs/architecture/MODULE_BOUNDARIES.md)为准。

| Package | 分类 | 职责 | 状态 |
|---|---|---|---|
| [contracts](contracts/README.md) | Public Contracts | 跨模块稳定类型与返回契约 | Experimental |
| [asset-package](asset-package/README.md) | Infrastructure | 当前 Catalog 内容与受管资源的确定性标准包交换 | Experimental |
| [authoring-kits](authoring-kits/README.md) | Infrastructure | 可移植 Agent 创作工具包、Contracts 同源版本规则与确定性导出 | Experimental |
| [core](core/README.md) | Application Core | 用例编排与持久 Job 状态机 | Experimental |
| [library-store](library-store/README.md) | Infrastructure | 用户级策略、信任、当前 Catalog 与自包含 Profile 持久化 | Experimental |
| [planting](planting/README.md) | Domain Capability | 策略作者文档、有效目标绑定与确定性资产分配 | Experimental |
| [profile-package](profile-package/README.md) | Infrastructure | RoleBindingProfile 的确定性 `.vfp` 标准包交换 | Experimental |
| [project-store](project-store/README.md) | Infrastructure | 项目事务、锁与内容寻址对象存储 | Experimental |
| [worker-runtime](worker-runtime/README.md) | Infrastructure | 外部 Worker 跨平台进程树约束与终止 | Experimental |
| [blender-worker](../adapters/blender-worker/README.md) | Integration Adapter | Blender 子进程监督与协议映射 | Experimental |
| [dts](../adapters/dts/README.md) | Integration Adapter | DTS ModelPoint XML 输出映射 | Experimental |
| [geospatial-worker](../adapters/geospatial-worker/README.md) | Integration Adapter | SHP/GeoJSON 区域输入、选区 SHP 编码与 GeoTIFF 高程采样 | Experimental |
| [model-api](../adapters/model-api/README.md) | Integration Adapter | OpenAI、Anthropic 与 DeepSeek 结构化模型协议映射 | Experimental |
| [strategy-worker](../adapters/strategy-worker/README.md) | Integration Adapter | trusted-local 策略包完整性校验与独立进程执行 | Experimental |
| [core-host](../apps/core-host/README.md) | App | 单项目组合根与执行循环 | Experimental |
| [dts-live-connector](../apps/dts-live-connector/README.md) | App | DTS Explorer Live Session 的认证 loopback 传输 | Experimental |
| [dts-live-webui](../apps/dts-live-webui/README.md) | App | Explorer 内嵌策略选择、多面种植与选区导入导出面板 | Experimental |
| [system-host](../apps/system-host/README.md) | App | 用户库与多项目 owner thread 生命周期 | Experimental |
| [local-api](../apps/local-api/README.md) | App | SystemHost 的 loopback HTTP 传输 | Experimental |
| [studio-service](../apps/studio-service/README.md) | App | Studio 专用只读投影、分块与临时缓存 | Experimental |
| [studio](../apps/studio/README.md) | App | Vue Renderer 与可替换 PlatformBridge | Experimental |
| [role-guide-ui](role-guide-ui/README.md) | App / Shared Presentation | 通用用途卡片、搭配建议与声明式构图示意 | Experimental |
| [strategy-preview-ui](strategy-preview-ui/README.md) | App / Shared Presentation | 通用参数示例画布、显隐与请求生命周期；由应用注入授权 transport | Experimental |
| [studio-electron-probe](../apps/studio-electron-probe/README.md) | App | Electron 桌面 PlatformBridge 能力探针 | Experimental Probe |
| [cli](../apps/cli/README.md) | App | 开发、验证与 Plant Run 命令行入口 | Experimental |

新增 package 时同步更新本表、对应的根 Python/pnpm workspace 和 package 自身
README。计划中的包不提前建立空目录，等对应契约进入实现阶段再加入。

## asset-browser-ui

Shared Vue multi-catalog browser for Studio and WebUI. Owns presentation state, local scope preferences and request lifecycle; receives application transport and thumbnails. Does not own catalog merging, candidate assignment, package schemas or execution.

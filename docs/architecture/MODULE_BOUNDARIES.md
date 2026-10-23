# 模块边界

依据：[ADR-0001](../adr/0001-module-and-application-boundaries.md)。

| 层级 | 目录 | 职责 |
|---|---|---|
| Contracts | `packages/contracts` | 跨包值对象、命令、事件和版本化文档 |
| Domain | `packages/planting` | 角色、参数、绑定校验和确定性资产分配 |
| Infrastructure | `packages/project-store`、`library-store`、`worker-runtime` | 存储、锁、资源和进程树 |
| Exchange | `packages/asset-package`、`profile-package`、`authoring-kits` | 受限包交换与创作资料 |
| Core | `packages/core` | 用例、Job 状态机、谱系校验和能力端口 |
| Adapter | `adapters/*` | 外部协议、Worker 和格式转换 |
| Host | `apps/core-host`、`system-host` | 单项目和机器级组合、线程所有权及生命周期 |
| Transport | `apps/local-api`、`dts-live-connector`、`cli` | 认证、传输与命令映射 |
| Presentation | `apps/studio`、`dts-live-webui`、`studio-service` | 页面、只读投影及可丢弃展示缓存 |
| Shared UI | `packages/role-guide-ui`、`strategy-preview-ui` | 两个 Renderer 共用的通用展示 |
| Desktop | `apps/studio-electron-probe` | 受控本机能力和桌面进程托管 |

下层不能依赖上层。Core 不导入具体 Adapter；Host 通过公开端口组合它们。
外部 SDK 类型在 Adapter 边界终止，UI 不直接访问数据库或内部对象路径。

## 数据所有权

- ProjectStore 持有不可变 Source、Region、PointSet、Assignment 和 Export 事实及引用。
- LibraryStore 管理策略、资产目录、植被搭配与受管缩略图；机器配置和密钥另行保存。
- SystemHost 管理线程、运行环境及活动会话；查询和取消不等待长任务占用的执行线程。
- 策略包持有空间算法、角色、参数、用途示意和参数示例地块，不感知平台资产目录。
- 应用负责浏览、选择、交互与图形绘制，不按策略 ID 建立算法或说明资料表。
- 参考半径及缩放影响示例绘制和搭配解析，不触发策略重算，不修改正式项目事实。
- SystemHost 解析方案兼容策略；Contracts 持有版本规则，Planting 校验角色与参数，
  Core 冻结实际执行输入。查询不写回方案，版本兼容不继承包信任。
- 桌面更新属于 Electron，首次使用引导属于 Studio；两者不新增 Core 或策略职责。

## 新增模块

模块必须有稳定职责、明确公共入口和独立验证方式。每个 workspace 包维护 README，
包含职责、公共入口、依赖边界、验证、当前限制及维护责任，并在模块索引登记。
不按页面数量机械拆包，也不为尚无消费者的能力增加空转发层。

测试会检查 workspace 一致性、维护 README 和 Python 依赖方向；所有权与业务语义仍需评审。

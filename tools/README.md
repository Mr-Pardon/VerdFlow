# 开发与验证工具

这里只维护可重复使用的开发、构建和集成验证入口。工具不进入 Core 业务调用链；
正常使用客户端不需要运行这些脚本。

## 开发与交付

| 入口 | 职责 |
| --- | --- |
| [dev_studio.mjs](dev_studio.mjs) | 启动并清理本轮 Studio、Local API 和 Electron 开发进程 |
| [build_branding.mjs](build_branding.mjs) | 从品牌原稿生成尺寸固定的应用素材 |
| [windows_branding.mjs](windows_branding.mjs) | 为 Windows 可执行文件写入图标和产品信息，供构建调用 |
| [build_portable_release.py](build_portable_release.py) | 构建 Windows x64 免安装包、校验信息和更新描述 |
| [portable_licenses.py](portable_licenses.py) | 收集实际随包依赖的第三方声明，供构建调用 |
| [inspect_resource_center.py](inspect_resource_center.py) | 静态检查资源中心真实附件，输出 v2 发现协议的审核事实；不执行策略、不导入 Library |

固定 Python wheel 清单见 [portable-requirements.txt](portable-requirements.txt)，品牌回归见
[branding.test.mjs](tests/branding.test.mjs)。

`inspect_resource_center.py --resources-root <资源仓库目录> --output <检查记录.json>` 使用本工作区
的包读取器及 Python 依赖，按已审核条目的固定 Release 地址受控下载。`--packages-root`
可改为读取本地已下载附件。临时下载自动清理，输出仅含公开资源信息和内容指纹；提交资源
仓库前需审核，运行工具不触发部署。契约见 `docs/architecture/RESOURCE_IMPORTS.md`。

## 按需集成验证

以下探针不能用普通单元测试替代，也不在客户端启动时运行。它们使用独立临时项目；
运行前阅读对应模块说明，不能指向唯一一份用户项目或正式 Library。

| 入口 | 验证范围 |
| --- | --- |
| [project_store_probe.py](project_store_probe.py) | 调用方提供文件的去重、重新打开和内容完整性 |
| [project_store_recovery_probe.py](project_store_recovery_probe.py) | 强制终止写入者后的持久化恢复 |
| [job_runtime_recovery_probe.py](job_runtime_recovery_probe.py) | 任务恢复、租约和旧执行者隔离 |
| [strategy_worker_recovery_probe.py](strategy_worker_recovery_probe.py) | 策略 Host/Worker 进程回收及恢复 |
| [blender_worker_probe.py](blender_worker_probe.py) | 真实 Blender 启动、协议、规范坐标和退出清理 |
| [blender_worker_recovery_probe.py](blender_worker_recovery_probe.py) | 真实 Blender 的 Host 崩溃与子进程回收 |
| [blender_capability_probe.py](blender_capability_probe.py) | 合成模型的材质、区域提取与高程采样 |
| [blender_source_assembly_probe.py](blender_source_assembly_probe.py) | 多成员 Source Assembly 与变换传播 |
| [blender_asset_catalog_probe.py](blender_asset_catalog_probe.py) | Blender Collection 到资产目录的契约；不代表已支持场景种植 |
| [model_api_material_probe.py](model_api_material_probe.py) | 显式指定 Provider 的在线材质分析；可能产生服务费用 |

Blender 探针需要可用 Blender；在线模型探针需要输入事实和环境变量凭据，只有显式调用
才会发起外部请求。结果默认保存在被忽略的 `tests/integration/results/`，不得提交机器路径、
凭据或业务模型信息。跳过或未执行集成验证不能记为通过。

## 夹具与维护

[Fixture Builders](fixtures/README.md) 提供参数化、坐标保持的测试数据构建能力。
一次性迁移脚本和阶段设计稿不作为日常验证前置条件；长期约束保存在 ADR 和契约测试中。
新增工具应说明输入、运行环境、产生的文件及清理责任；退役时同步清理测试与文档引用。

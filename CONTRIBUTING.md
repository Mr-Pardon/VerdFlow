# 参与 VerdFlow

欢迎提交缺陷、交互改进和扩展能力。报告问题时说明版本、操作步骤、预期与实际结果；
日志、截图和模型须先清除个人信息、机器路径与无权分发的数据。

GitHub 是主仓库，Gitee 作为单向源码镜像。请在 GitHub 提交代码与 Pull Request，避免直接修改镜像分支。
镜像范围和维护方式见 [Gitee Mirror](docs/development/GITEE_MIRROR.md)。

## 开发流程

1. 阅读 [模块边界](docs/architecture/MODULE_BOUNDARIES.md) 和目标包 README。
2. 为修改增加适当的边界测试，不在通用层写入某个策略或平台的特例。
3. 按 [开发与测试](docs/development/TESTING.md) 验证，并说明未覆盖的运行环境。
4. 跨模块契约或设计取舍变化时补充 ADR，明确替代关系。

提交代码应有权按 Apache-2.0 提供。第三方代码、模型与素材应保留来源和许可，
不得把仅供某个商业平台使用的资源当作 VerdFlow 自有资产再分发。

发现可能泄露凭据或可利用的安全问题时，请按 [安全反馈](SECURITY.md) 使用 GitHub
私密漏洞报告，不要在公开问题中粘贴凭据、客户数据或完整利用材料。

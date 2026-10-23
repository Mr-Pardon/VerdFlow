# Profile Package

维护责任：VerdFlow Core Team

`verdflow-profile-package` 只负责一个不可变 RoleBindingProfile 的确定性 `.vfp`
交换格式，不依赖 LibraryStore、Planting、SystemHost 或界面。

## 职责

- 规范化写出 `manifest.json` 与 `profile.json`；
- 限制并校验 ZIP 结构、大小、Profile 身份和内容 hash；
- 携带交换基线的精确策略 package hash；Catalog 仅保留可选来源链接。

## 公共入口

- `write_role_binding_profile_package()`
- `inspect_role_binding_profile_package()`

## 依赖边界

只依赖 `verdflow-contracts`。Profile 领域 schema 校验、依赖解析、LibraryStore 提交和
当前 Profile 的显式更新由 SystemHost 负责，不创建版本树。
可选 `strategy_parameters` 与其余 Profile 内容一起参与 canonical JSON 和 hash；
只交换数值，不交换参数定义或随机种子。策略契约校验由 SystemHost/planting 完成。
ADR-0016：接收端可以解析更高的同 major 兼容策略。导出端用实际可用策略创建临时
交换基线，不修改本地方案；本包仍只验证归档内的精确身份和 hash，不选择版本或信任包。

## 验证

运行 `python -m unittest tests.unit.test_profile_package`。

## 当前限制

包不交换策略、Catalog、资产资源、项目状态或信任决定；首版不提供聚合交付包、
Profile 草稿或跨策略迁移。

# Asset Package

维护责任：VerdFlow Core Team

`verdflow-asset-package` 只负责当前目标 Catalog 内容与其引用资源的不可变可移植快照，
交换格式，不依赖 LibraryStore、Planting、SystemHost 或界面。

## 职责

- 确定性写出 `.vfa`；
- 限制并校验归档结构、大小、Catalog 身份、hash 和资源闭包；
- 逐个流式返回已校验资源，避免把整个包常驻内存。

## 公共入口

- `write_asset_catalog_package()`
- `inspect_asset_catalog_package()`
- `read_asset_catalog_package()`
- `catalog_resource_ids()`

## 依赖边界

只依赖 `verdflow-contracts`。Catalog 领域 schema 校验、LibraryStore 当前内容提交、冲突确认
和 HTTP/Studio 编排由 SystemHost 负责。导出包不在本地建立 Catalog 版本。

## 验证

运行 `python -m unittest tests.unit.test_asset_package`。

## 当前限制

包不交换工作库、RoleBindingProfile、策略包或项目运行状态；首版单资源上限 16 MiB，
归档展开后上限 512 MiB。

# DTS Catalog 契约

编写文档使用 `verdflow.target-asset-catalog` version `1`。

根对象必填字段：

- `catalog_id`：稳定的小写标识；
- `target_type`：`dts`；
- `provider_id`：`dts.static-mesh`；
- `display_name`：面向用户的名称；
- `assets`：资产记录。

每项资产必须包含 `asset_id`、`display_name` 和 `provider_metadata`。对 DTS 而言，`provider_metadata.model_name` 是输出时使用的精确静态网格体标识。它必须作为字符串从证据中复制，不得根据植物学名称推导。

`asset_id` 只要求在本 Catalog 内唯一，不需要为跨库种植自行增加全局前缀。VerdFlow 在
选择时生成独立的搭配 ID，并保留库内原始 ID；不要把界面中的搭配内部 ID 回填到资产包。

可选字段：

- `thumbnail`：编写工作区内 PNG、JPEG 或 WebP 的相对路径。`build_vfa.py` 会把它转换为按内容寻址的 `verdflow-resource:` 引用；
- `suggested_scale`：使用 `{mode: fixed, value: N}`，或 `{mode: uniform_random, min: N, max: N}`。数值必须为正且 `min <= max`；
- `reference_radius_m`：目标资产在统一缩放 `1.0` 时的 XY 平面参考影响半径，单位为米；数值必须有限且大于 `0`；
- `reference_height_m`：目标资产在统一缩放 `1.0` 时的参考高度，单位为米；数值必须有限且大于 `0`，不是地形高程、海拔或种植点 Z 坐标；
- `tags`：用于搜索的词，不是策略 Role；
- `collections`：面向用户的资产分组，其中的 `asset_ids` 必须存在于同一 Catalog。

参考半径与参考高度独立可选。没有可信尺寸证据时省略字段，不填 `0` 或 `null`，也不依据植物学常见尺寸补值。模板中的数值仅为格式示例，不应原样套用到其他资产。

打包后的 `.vfa` 包含规范化的 `catalog.json`、`manifest.json` 和按内容寻址的资源。不要手动修改包内部文件；应运行随附构建器，并通过 VerdFlow 的正常校验入口导入结果。

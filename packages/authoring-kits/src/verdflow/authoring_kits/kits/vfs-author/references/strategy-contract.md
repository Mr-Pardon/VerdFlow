# 策略契约

## 包内文件

`strategy-package.json` 是分发清单。它的 `files` 数组必须包含清单自身以外的所有分发文件，并按 POSIX 相对路径排序。符号链接、未声明文件、绝对路径、`..`、反斜杠路径和大于 16 MiB 的文件都会被拒绝。

清单必须包含以下值：

- `schema`: `verdflow.strategy-package`
- `version`: `1`
- `runtime`: `python`
- `api_version`: `1`
- `strategy_file`: usually `strategy.yaml`
- `entry`: usually `generator.py:generate`

`package_id` 必须等于 `strategy.strategy_id`，`package_version` 必须等于 `strategy.strategy_version`，清单中的 `entry` 必须等于 `strategy.generator.entry`。

版本必须遵守 `references/versioning.md` 的 SemVer 兼容规则。方案允许同 major 的
正式版本向上复用，不要求策略声明逐个兼容版本；破坏角色或参数语义必须升 major。

## 生成器输入

生成器接收一个包含以下字段的对象：

- `strategy`：已通过校验的 Strategy v2 数据；
- `selected_regions`：具有稳定 ID 和多边形环的 Region 对象；
- `seed`：整数随机种子；
- `args.strategy_parameters`：由 `generation_parameters` 声明的可选用户覆盖值；
- `excluded_role_keys`：已关闭的 Role key 列表，缺省或空列表表示所有 Role 启用。

遇到未知覆盖参数时必须明确失败，不得静默接受拼写错误的控制项。

## 角色开关与构图

`excluded_role_keys` 属于生成输入，不是 Profile 或目标资产信息。策略可以忽略它而完整
生成，也可以跳过关闭角色的计算，或根据角色状态重新组织其余层次。Core 无论策略是否
处理开关，都会在结果定版前过滤关闭角色；Core 不负责补点、重排或修复构图。

设计前向用户说明每种合法开关组合的空间效果，尤其是关闭锚点、铺底或支撑角色后其余
角色的处理。使用这个字段时校验列表中的 key 已声明且不重复，不要读取 Profile、权重、
缩放或 Catalog。至少保留一个启用角色是产品配置边界；不要依赖未传入的资产数据。

相同区域、参数、种子、角色开关和策略包必须可复现，不同开关组合可以产生不同的点位。
`extensions.selective_role_generation.stability: filter_equivalent` 仅是可选作者承诺，
表示任意保留角色点位都与完整生成后过滤逐点一致（包括 ID、顺序与旋转）。只有覆盖此
测试的策略才可声明；会调整覆盖基准或重排构图的策略不能声明。没有声明不影响执行，
也不需要应用增加策略能力分支。

根据角色状态推导的内部有效值只在算法内使用，不改写用户参数、Profile 或命令。例如
关闭铺底后把主景覆盖基准视为整面，是策略计算规则，而不是要求应用把铺底覆盖率改成
100。用户重新启用铺底时应恢复原值。修改开关行为须发布新策略版本，保留旧执行快照。

## 生成器输出

返回 `verdflow.points` version `2`。每个点必须包含：

- 稳定的 `id`、`region_id` 和 `role_key`；
- 采用 `[x, y, z]` 格式的 `generated_position`；
- 高程采样前为 `null` 的 `terrain_sample`；
- 初始值等于生成位置的 `resolved_position`；
- `rotation_quaternion_xyzw`。

除非 Host 契约明确变更，否则使用 `coordinate_space: source_scene_world`、`offset_applied: false`、`kind: generated` 和 `resolution_policy: generated_position`。

## Role 字段

在不损失构图关系的前提下使用尽可能少的 Role。只有当某类点位具有独立空间职责，或需要独立开关、控制、统计或映射时，才新增 Role。

- `role_key` 是稳定的机器标识。策略发布后不要仅因文案调整而修改它。
- `display_name` 和 `description` 应说明空间职责，不应写成具体资产名称。
- `required` 表示启用该 Role 时必须有完整映射，并不禁止用户明确关闭它。
- `assignment_policy: single` 表示预期只映射一个资产候选。
- `assignment_policy: weighted_pool` 表示允许多个空间上可互换的资产按权重分配。

Role 的完整判定方法见 `role-modeling.md`。Strategy 不得包含 Catalog ID、Profile ID、DTS 模型名、Blender 对象名或缩略图引用。

可选的用途分组、推荐素材与构图示意放在包内 `extensions.role_presentation`，格式和设计
方法见 `role-presentation.md`。它只负责帮助用户理解 Role，不改变生成、映射或开关契约，
也不需要应用层为策略增加适配。名称与说明仍以 `roles` 为唯一来源。

## 参数边界

`generation_parameters` 是 Studio、实时种植面板或其他应用入口可展示给用户的公共控制面。每个参数都应包含明确的默认值、类型、边界与效果说明。调整参数应改变空间结果，而不应改变数据契约。

内部实现常量放在 `parameters`。不要为了暴露更多控制项而把每个算法常量都变成公共参数；第一版只暴露用户能理解、能观察结果且能安全调整的参数。

参数可选声明 `visible_for_roles: [role_key, ...]`：关联角色中至少一个启用时展示，全部关闭
时隐藏；不声明则始终展示。数组必须非空、不重复，并引用本策略已有 Role。定义由策略包
持有，应用不按参数名或策略 ID 推断关联。关闭角色只隐藏控件，不清除值；重新启用时
恢复原值，Profile 与执行仍保留完整参数。该展示声明不决定算法行为；角色感知构图由
生成器单独处理，Core 始终后过滤。

必须依据算法的实际影响面设计关联。例如，某参数同时影响中心乔木出现概率和周边灌木的
分布半径，应关联所有相关 Role，而不是只写中心乔木。涉及整体斑块或共享支撑区的参数
通常不声明角色条件；若策略在关闭该 Role 时明确不再使用某个共享参数，也可把它关联
该 Role，并说明关闭后的计算基准。不能在应用里按参数名猜测这种语义。

Profile 可以可选保存公开参数值，方便用户复用植被搭配与构图。但定义始终只在策略包，
生成器不得读取 Profile；它仍只读取执行上下文中的 `args.strategy_parameters`。
执行侧按策略默认值、Profile 预设、本次覆盖的顺序解析。随机种子只属于本次运行。
不要把密度、间距等运行参数改写成 Role 或资产字段，也不要把 Profile 参数预设当作算法依赖。

可选 `extensions.parameter_preview` v1 为原生成器提供一个固定样本地块，让用户观察参数
变化。格式、尺度和预算见 `parameter-preview.md`。它不改变正式生成入口，不允许独立脚本、
图片或 Studio 专属算法；样本与固定种子不进入 Profile。

# 植被用途与构图示意

角色由策略定义，展示内容也必须由策略包持有。用户不需要知道 `role_key` 的技术含义，
但应能从显示名、说明和示意看出“这类植被放在哪、与其他植被有什么关系”。

## 设计方法

1. 先在 `roles[].display_name` 和 `description` 写清空间用途。例如“铺底植被”“中心主景”，
   不写具体模型或物种 ID。一个用途可以映射一种或多种承担相同职责的资产。
2. 按空间关系分组，例如铺底/组团，或不同局部宽度下的排布。分组只组织面板，不能改变
   Role 的 `required`、`assignment_policy` 或生成规则。
3. 提供可选素材类型建议，说明大小、层次和搭配关系；建议不是强制的生物分类或资产过滤。
4. 画一个静态构图示意，突出中心、外围、边缘、沿线等位置关系。它不是当前选区的实际
   生成结果，符号大小也不是植物半径、米制尺寸或覆盖率承诺。
5. 面板可以自动强调当前选中的 Role，Agent 不需要编写组件或 CSS。新增策略不得修改
   Studio、SystemHost、Core 或实时面板，也不得建立应用层 ID/hash 到示意的注册表。

## 声明格式

Strategy v2 和 `.vfs` 分发清单保持现有版本。展示使用可选的扩展声明：

```yaml
extensions:
  role_presentation:
    version: '1'
    roles:
      ground:
        suggested_assets: [草花, 地被, 矮灌木]
        note: 可选择尺度相近的一种或多种植被。
      main:
        suggested_assets: [乔木, 较高灌木]
        note: 与铺底植被形成高低关系，不限定具体物种。
    groups:
      - id: community
        name: 自然群落
        description: 低矮植被铺底，主景形成体量。
        role_keys: [ground, main]
        diagram:
          - {role_key: ground, kind: area, points: [[5, 5], [95, 5], [95, 57], [5, 57]]}
          - {role_key: main, kind: tree, x: 50, y: 30, size: 16}
```

此示例要求策略的 `roles` 已声明 `ground` 和 `main`，名称与职责只写在 `roles` 中，
不要在展示扩展里再次定义。展示扩展的 `roles` 必须完整覆盖角色集合，每个角色恰好
属于一个组；即使没有推荐内容，也保留该角色的空对象 `{}`。

- `version` 必须是字符串 `'1'`。
- 组的 `id`、`name` 和非空 `role_keys` 必填；`description`、`diagram` 可省略。
- `order` 为可选的 0 至 32 整数，默认按组数组顺序；组内沿用 `roles` 的声明顺序。
- `suggested_assets` 是最多 8 项纯文本建议；`note` 可省略。所有文本最多 500 字符。
- 每组最多 128 个标记，最多 32 组。标记只能引用已声明的 `role_key`。
- 画幅为 `100 x 62`，坐标为归一化示意坐标，不是世界坐标。
- `area` 的 `points` 包含 3 至 32 个二维顶点，x 在 0 至 100，y 在 0 至 62。
- `tree`、`shrub`、`groundcover` 是通用符号，使用 `x`、`y` 和 0 至 24 的 `size`。
- 不包含 HTML、脚本、SVG 字符串、远程 URL、资产缩略图或目标平台 locator。

没有声明或声明不合规时，应用回退基本角色展示，不阻断策略生成。但作者仍应在交付前
验证示意，而不能把“算法可执行”当作“展示已正确”的证明。

## 更新与验证

修改名称、说明或示意也会改变策略包 hash。已导入版本的内容变化应发布新的包版本，并
同步 `strategy_version` 与清单的 `package_version`；不要覆盖已安装包或复用旧信任。
已有项目冻结的代码与 Profile 不会自动升级。

至少检查角色覆盖、组唯一性、坐标与文本限制，以及导出再导入后的声明完整性。在可用的
Studio/实时面板中检查卡片名称、示意位置与推荐表达；无法运行面板时说明尚未视觉验证。
`assets/build_vfs.py` 保证归档结构，不替代 Strategy v2 与展示声明的完整验收。

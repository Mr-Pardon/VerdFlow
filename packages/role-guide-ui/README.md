# verdflow-role-guide-ui

状态：Experimental  
架构分类：App / Shared Presentation  
维护责任：Studio and DTS Live UI maintainers

## 职责

通用植被用途导航、禁用状态、搭配建议与声明式构图示意。Studio 和 DTS Live 共用，不按策略 ID
判断布局，也不解释生成规则、宽度阈值或资产是否适合执行。

## 公共入口

`RoleChoiceNav`、`RolePurposePanel`、`RoleDisabledState` 及其只读 props 类型由包根导出。

## 依赖边界

- 只依赖 Vue 与现有图标库，不依赖 Core、Python、API 客户端或任何 App。
- 不写角色配置，不发请求；选择用途只向调用方发出 role key。
- 不加载自定义脚本、SVG 字符串、HTML 或远程示意资源。
- 名称、分组、推荐和示意坐标只来自策略包声明；本包只持有通用符号、配色与排版，
  不按策略 ID 或 hash 查表，不内置任何策略的专属展示资料。

## 验证

在 Studio 和 DTS Live 运行类型检查、构建及用途导航浏览器回归。

## 当前限制

- 示意是静态构图资料，不是当前选区生成结果或资产冠幅承诺。
- 配置、缩略图、权重、缩放与角色启用仍由所属应用管理。

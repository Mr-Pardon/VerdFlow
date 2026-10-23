# 测试与验证

测试以当前模块契约为依据，不要求旧工程、历史运行报告或客户模型。

## 日常回归

```powershell
python -m unittest discover -s tests/unit
corepack pnpm --filter @verdflow/studio test
corepack pnpm --filter @verdflow/dts-live-webui test
corepack pnpm --filter @verdflow/studio-electron-probe check
corepack pnpm typecheck
```

- `unit/` 覆盖持久化、任务恢复、坐标、策略、资产映射、API 和交付边界。
- 各前端自己的 `tests/` 覆盖交互状态、组件生命周期和依赖方向。
- [集成工具](../tools/README.md) 覆盖真实外部进程、平台协议和恢复行为，按环境显式运行。
- `integration/helpers/` 是测试和探针使用的故障注入进程或合成模型生成器，不是用户入口。
- [结果目录](integration/results/README.md) 只用于本地证据；[夹具目录](fixtures/README.md)
  不存放无权分发的模型或真实业务数据。

Windows 完整进程测试应使用实际 CPython。虚拟环境转发器的 PID 差异不能通过放宽
进程身份校验规避；缺少运行环境的跳过和未执行项必须单独说明。

## 保留与退役

不能因为测试名称带阶段号、策略有旧版本或命令未被 UI 调用就删除测试。只要仍保护
受支持契约、确定性输出、兼容边界或失败恢复，它就有维护价值。Strategy 包的不同 major
可能有不同角色和参数，不能将其测试当作重复覆盖删除。

只有对应工具或能力明确退役、且没有生产入口、导出声明、动态加载或其他测试依赖时，
才一起删除专属测试。不能通过删除失败断言、扩大容差或更新快照掩盖回归。
测试只能清理自己创建的临时数据，不能修改已有用户 Library、项目或外部场景。

# verdflow-strategy-worker

状态：Experimental  
架构分类：Integration Adapter  
维护责任：Strategy execution boundary maintainers

## 职责

检查本地策略包 manifest、路径、声明文件和 package hash，并且只在调用方显式信任
该精确 hash 后，通过独立 Python 子进程调用生成器。子进程输出必须通过
`verdflow.points` v2 校验才能返回调用方。`StrategyWorkerSupervisor` 领取持久 Job，
在执行时复核用户信任、从 RegionSet Artifact 物化选区、周期续租，并把 PointSet 与
确定性策略包 ZIP 快照登记为项目 Artifact。每次外部进程还会登记精确
`WorkerProcess` provenance，并通过共享 `verdflow-worker-runtime` 约束进程树；Host
重启时只收敛本 Adapter 的记录、清理对应 Attempt cache，再由 Job lease 决定重排。
`ProjectStrategyDocumentProvider` 安全展开并重新检查项目冻结的策略 ZIP，为 Core
提供规范策略文档；它不从可变 User Library 包目录读取复现事实。
生成命令中的 `excluded_role_keys` 原样传给策略 context；按 ADR-0004，策略可忽略、
跳过关闭 Role，或自行调整剩余构图。Core 始终在结果定版前统一过滤，不检测处理能力。
ADR-0004 的 `filter_equivalent` 仅为可选作者承诺，不作为执行分发条件。

本包只支持 `trusted-local` 执行模型。它隔离 Core 崩溃与全局 import 状态，但不是 OS
安全沙箱，不能阻止恶意 Python 读取用户文件、联网或启动子进程。
`StrategyWorkerConfig.timeout_seconds` 默认为 `None`，允许可信本地策略长时间计算；
自动化或测试调用方可显式配置有限正数，届时仍按原机制终止超时进程树。
无总执行上限不取消心跳续租、用户取消、异常退出检测、启动 gate 和宿主退出清理。
心跳由父进程监督循环发送，只证明监督链路存活，不代表生成器的实际进度；现有
阶段进度不能解释为算法完成比例，也不引入缺乏算法进度协议的默认空闲截止时间。
见 ADR-0003。

线程安全取消信号仅记录意图，不在 HTTP 线程写项目。Supervisor 在所属线程确认取消前
先通过 JobRuntime 登记 `cancelling`，再登记 `cancelled`；即使 owner mailbox 尚未处理
顶层取消命令，也不依赖租约到期收尾。进程退出后但结果登记前收到信号同样丢弃本次输出。

## 公共入口

```python
from verdflow.strategy_worker import (
    StrategyPackage,
    ProjectStrategyDocumentProvider,
    StrategyWorker,
    StrategyWorkerConfig,
    StrategyWorkerSupervisor,
    StrategyWorkerSupervisorConfig,
    inspect_strategy_package,
    open_strategy_package_archive,
    write_strategy_package_archive,
)
```

调用方信任 `StrategyPackage.package_hash`，不能只按 package ID、目录名或作者名称
信任。执行前 Worker 会重新检查包，源码变化后旧信任立即失效。

## 依赖边界

- Runner 依赖 `verdflow-planting`、`verdflow-worker-runtime` 与 Python 标准库；
  Supervisor 通过 Core Runtime 和 ProjectStore 公共门面完成 Job/Artifact 交接。
- 不直接打开 SQLite，不持有用户信任库，不直接接入 UI、MCP 或 DTS。
- 不依赖具体策略包，也不在 Core 进程内 import 生成器。
- `trusted-local` 不得描述为 sandboxed 或 safe-for-untrusted。

## 内部结构

```text
package.py       manifest、路径、文件集合与 package hash 校验
documents.py     项目策略 ZIP 的受限展开、精确 hash 复核与规范文档读取
runner.py        精确 hash 信任、私有工作目录、最小环境和受控进程树生命周期
protocol.py      共享命令身份与 Supervisor 配置
supervisor.py    Job 授权、Region 物化、进程 provenance、恢复、取消和结果登记
package_archive.py `.vfs` 与项目快照共用的有界、确定性策略归档读写
worker_entry.py  子进程内的生成器加载与 JSON 文件协议入口
preview.py       已授权示例的有界进程执行、取消与二维结果校验
preview_entry.py 原生成器的独立展示投影入口，不发布正式 PointSet
errors.py        包完整性、信任和执行错误
```

## 验证

```powershell
python -m unittest tests.unit.test_strategy_worker -v
python -m unittest tests.unit.test_strategy_worker_supervisor -v
python -m unittest tests.unit.test_worker_runtime -v
python tools\strategy_worker_recovery_probe.py
python -m unittest discover -s tests\unit -v
```

## 当前限制

- 没有 OS 权限沙箱、代码签名、发布者身份、撤销列表或策略市场信任链。
- trusted hash 已通过 Core 只读端口接入持久 LibraryStore；Studio 只对精确 hash 提供人工信任。
- 首版只支持 Python、`api_version=1` 和 `verdflow.points` v2 返回值。
- 项目策略快照读取限制为 1024 个条目和 64 MiB 解压后大小；仅在项目 runtime
  临时目录展开并在调用结束后清理。该机制只用于策略代码包，不用于大型模型资产库。
- Windows 已验证 Host 强杀后整个策略进程树回收、Attempt 重排和第二次执行；POSIX
  父进程崩溃后的孤儿防护仍需独立平台验证。
- 只允许团队与用户明确审核并信任的本地策略包执行。

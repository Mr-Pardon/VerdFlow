# verdflow-worker-runtime

状态：Experimental  
架构分类：Infrastructure  
维护责任：External worker runtime maintainers

## 职责

提供外部 Worker 共用的跨平台进程树约束：Windows Job Object、POSIX process group、
受控终止和按已验证 PID 回收。它只处理操作系统进程，不认识 Job、Attempt、
ProjectStore、Blender、Strategy 或 Agent 协议。

## 公共入口

```python
from verdflow.worker_runtime import ProcessTreeGuard, terminate_process_by_pid
```

## 依赖边界

- 只依赖 Python 标准库。
- Adapter 负责在调用本包前验证进程身份，并负责 WorkerProcess provenance。
- Core 和领域包不依赖本包，也不接触 PID 或平台进程句柄。

## 验证

```powershell
python -m unittest tests.unit.test_worker_runtime -v
python tools\strategy_worker_recovery_probe.py
```

## 当前限制

- 本包提供进程树生命周期约束，不提供 OS 权限沙箱。
- 按 PID 回收必须由调用方先使用稳定进程身份校验，不能对未确认 PID 直接调用。
- Windows 是当前强杀恢复主门禁；其他平台保留 process-group 实现和单元覆盖。

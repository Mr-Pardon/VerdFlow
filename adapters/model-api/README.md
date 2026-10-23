# verdflow-model-api

状态：Experimental  
架构分类：Integration Adapter  
维护责任：Model API integration maintainers

## 职责

把版本化 VerdFlow 分析任务映射到 OpenAI Responses、Anthropic Messages 和 DeepSeek
OpenAI-compatible Chat Completions，并把供应商响应收敛为同一种严格候选契约。该包
只负责 API 协议、认证头、响应提取和本地候选校验，不决定候选是否成为项目事实。

## 公共入口

```python
from verdflow.model_api import (
    EnvironmentSecretResolver,
    ModelApiClient,
    ModelApiProviderRegistry,
    ModelApiWorkerConfig,
    ModelApiWorkerSupervisor,
    parse_provider_config_document,
    provider_presets,
)
```

`ModelApiClient.analyze_materials()` 接收 Blender MaterialFacts 文档和显式 API Key，
返回 `MaterialClassificationCandidate`。调用方应通过 `SecretResolver` 提供密钥；Client
本身不读取项目文件或全局环境。

`ModelApiWorkerSupervisor` 是持久 Job 的外部进程入口。它在私有临时目录准备事实报告，
通过子进程环境短暂注入密钥，登记 `WorkerProcess`，收敛 ready、心跳、取消、超时和
规范结果。命令、项目 Artifact、诊断和日志均不保存密钥；调用计划、心跳与远端
request ID 分属不同协议证据。只要请求可能已经发出，失败即标记为不确定且禁止隐式
重试。

OpenAI、Anthropic 和 DeepSeek 是不固定模型的协议模板，不是白名单。用户可以提供
版本化 JSON 配置，选择已有协议、HTTPS 推理端点、可选模型发现端点和初始模型，然后按
`provider_id` 选择使用：

```python
custom = parse_provider_config_document(
    {
        "schema": "verdflow.model-api-provider-config",
        "version": "1",
        "providers": [
            {
                "provider_id": "team-gateway",
                "display_name": "Team Gateway",
                "protocol": "openai-chat-completions",
                "endpoint": "https://models.example.com/v1/chat/completions",
                "model_discovery_endpoint": "https://models.example.com/v1/models",
                "default_model": "team-material-model",
                "secret_ref": "model-api/custom/team-gateway",
            }
        ],
    }
)
client = ModelApiClient(provider_registry=ModelApiProviderRegistry(custom))
```

自定义 Provider 不能覆盖内置 ID，不能注入任意请求头、Python 类或脚本。用户仍可在
每次任务中显式覆盖所选 model；实际 Provider 配置版本和 model 会进入候选 provenance。
`ModelApiClient.list_models()` 只提供候选列表，不自动选择或验证模型；未提供发现端点时
仍可手动填写模型 ID。

## 依赖边界

- Client 与协议映射只依赖 `verdflow-contracts` 和 Python 标准库；Supervisor 额外依赖
  Core JobRuntime、ProjectStore 和共享 Worker Runtime。
- 不依赖 LibraryStore、Blender、MCP 或供应商 SDK。
- 内置 Provider 不携带模型预设；机器级选择与每次结果的实际 model 分别记录。
- 不保存原始响应或思维链，也不在异常中回显敏感请求头。
- 正式端点必须是 HTTPS；测试只允许 loopback HTTP。
- 自定义 Provider 配置属于 System 范围，不进入单项目 ProjectStore。

## 验证

```powershell
python -m unittest tests.unit.test_model_api_adapter -v
python -m unittest tests.unit.test_model_api_worker_supervisor -v
python tools\model_api_material_probe.py --input <fact-report> --output <result> --provider deepseek
```

在线验证统一使用通用探针，从 Provider 对应的开发环境变量解析密钥；缺少凭据时拒绝请求，
命令行参数和结果文件均不包含密钥。仅在显式执行时调用外部模型，可能产生服务费用。
提交给外部模型的纹理引用保留安全的 Source 相对路径；旧绝对 `filepath` 和 Source 外部
纹理只保留文件名，不传输本地目录结构。

## 当前限制

- 持久 Job Supervisor 已接入可选 CoreHost Worker；Windows SystemHost SecretStore 已使用
  当前用户 Credential Manager，其他平台当前失败关闭。
- Provider Capability、动态 token 调用计划、DeepSeek 非思考模式和非完整结束原因校验
  已实现；成本阈值只作为调用计划告警，不触发固定分批。
- 自定义 Provider 的解析、校验、选择、调用和 SystemHost 持久化已实现；Studio 配置
  面板已接入。
- OpenAI 和 Anthropic 当前只有协议夹具门禁，尚未使用真实账户在线验证。
- DeepSeek JSON Output 官方说明存在偶发空内容风险；当前 Client 明确失败，不进行
  可能重复计费的隐式重试。
- 首轮只允许三种已实现协议；企业专有认证头和新协议需要独立 Adapter 评审。
- Codex Agent Provider 属于独立 Adapter，不在本包内实现。

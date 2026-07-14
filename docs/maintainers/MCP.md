# MCP 工具契约

MCP 是 Codex 和薄适配宿主共享的 stdio 工具边界。协议层只负责 JSON-RPC、schema 校验、错误映射和分发。

## 注册表是唯一来源

`worker_rights_cn/mcp/registry.py` 的 `_TOOL_SPECS` 定义公开名称、标题、说明和输入 schema。handler 必须来自 `worker_rights_cn.tools.TOOLS`。注册集合与 handler 集合不一致时应立即失败。

当前工具分为：

- intake：`worker_rights.validate_intake`
- calculation：`worker_rights.calculate_compensation`
- case package：`worker_rights.assemble_case_package`
- documents：`worker_rights.render_documents`、`worker_rights.export_bundle`
- audit：`worker_rights.audit_status`
- sources：`worker_rights.search_sources`、`worker_rights.plan_ai_recall`、`worker_rights.validate_ai_recall_response`、`worker_rights.prepare_embedding_index`
- termination and evidence：`worker_rights.map_termination`、`worker_rights.build_evidence_plan`
- final gate：`worker_rights.review_consultation_output`

## 稳定性规则

- 已发布工具名、字段含义和错误类别是兼容接口。
- 新增可选字段优先；删除或改义需要版本迁移。
- 所有输入先过 JSON schema，再进 domain handler。
- domain 异常转换为可行动错误，不向普通用户泄漏 traceback。
- `audit`、`audit_session_id` 和 `audit_db_path` 只在调用方显式请求时使用。
- `output_dir`、数据库路径和案件路径必须由调用方显式提供。
- AI recall 只生成或验证宿主网关请求，不让插件自行调用外部模型，也不接收原始秘密。

## 隐私与副作用

列工具、计算、分类和检索默认无案件写入。导出、artifact 记录和审计必须由明确参数触发。任何包含案件内容的返回值都继续受宿主输出复核和脱敏规则约束。

## 变更检查

修改 registry、server 或 handler 后，运行 MCP registry/server 回归、domain tool 回归、隐私回归和 host smoke。新增工具还要更新本文件和工具清单测试。

可安全查看入口脚本的现有接口：

```powershell
python plugins/worker-rights-cn/scripts/local_db.py --help
python plugins/worker-rights-cn/scripts/session_store.py --help
```


# 架构与模块契约

Worker Rights CN 是 Codex-first、local-first 的劳动者侧插件。Codex 定义规范行为。其他宿主只做路径、事件和配置转换。

## 数据流

用户普通中文 → 安全分类 → 案件路由 → 确定性工具与领域技能 → 输出复核 → 用户。

只有用户明确确认保存时，才进入脱敏预览和 `CaseStore`。MCP 不可用时，普通指导仍可继续，但计算、写入或导出失败必须明确说明。

## 模块 owner

这里的模块 owner 是代码责任边界，不是个人名单。

| owner | 路径 | 责任 | 不负责 |
| --- | --- | --- | --- |
| case-model | `worker_rights_cn/case_model.py` | 版本化案件结构、六状态校验、显式迁移 | 路由和持久化 |
| safety | `worker_rights_cn/safety/` | 请求分类、风险拦截、最终输出复核 | 业务计算 |
| orchestration | `worker_rights_cn/orchestrator.py` | 阶段、技能选择、缺失事实、保存确认路由 | 复制领域规则 |
| privacy | `worker_rights_cn/privacy.py` | 字段分类、脱敏预览、保存确认、删除验证 | 任意写盘 |
| case-storage | `worker_rights_cn/storage/cases.py` | 显式保存、加载、导出、删除、删除证明 | 公共知识索引 |
| knowledge-storage | `worker_rights_cn/storage/knowledge.py` | 公共来源、城市规则、检索索引 | 用户案件正文 |
| domain-tools | `worker_rights_cn/tools/` | 案情、补偿、证据、文档和来源的确定性处理 | 宿主协议 |
| MCP | `worker_rights_cn/mcp/` | stdio 协议、schema 和工具注册 | 法律逻辑 |
| skills | `skills/` | 触发说明、用户流程和领域输出契约 | 隐式持久化 |
| host-adapters | 宿主清单、hooks 与事件桥接 | 宿主格式转换和降级提示 | 复制安全、隐私、路由或法律规则 |

改动跨越 owner 边界时，必须同时运行双方的契约测试。

## 稳定接口

以下接口供统一入口和宿主边界使用：

- `worker_rights_cn.orchestrator.route_case(case, message)`
- `worker_rights_cn.safety.classify_request(case, message)`
- `worker_rights_cn.safety.review_output(case, draft)`
- `worker_rights_cn.privacy.redaction_preview(value)`
- `worker_rights_cn.privacy.confirm_save(request)`
- `CaseStore.save(case, consent)`、`export(case_id, destination)`、`delete(case_id)`、`deletion_proof(case_id, receipt)`

案件 schema 当前为 `worker-rights-case/1`，scope 为 `cn-mainland-worker-side`。任何字段语义变化都要新增显式迁移和回归，不能静默重写用户数据。

## 不变量

- 普通用户不需要选择内部技能。
- 首次有效答复固定四段顺序。
- 每个法律结论只能使用六状态之一。
- 默认无保存、无上传、无发送。
- 保存必须绑定已展示的绝对路径和范围。
- 公共知识库不存用户姓名、联系方式或证据正文。
- 删除案件不影响公共知识库。
- adapter 失败不影响 Codex 主流程。


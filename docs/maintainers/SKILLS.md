# 技能契约

## 统一公开入口

`skills/worker-rights-guide/SKILL.md` 是普通劳动者唯一需要触发的入口。它接收普通中文，不要求用户知道技能名。

入口必须读取 `references/output-contract.json`，然后调用安全分类和编排器。不要把 `orchestrator.py` 的路由条件复制到 Markdown。

## 首次有效答复

四个标题必须按此顺序出现：

1. 现在先不要做什么
2. 今天应当保存什么
3. 当前可能涉及哪些权益
4. 下一步需要补充什么信息

每个法律结论只能标记为 `confirmed_fact`、`supported_assessment`、`estimate`、`local_verify`、`lawyer_review` 或 `out_of_scope`。

## 专项技能责任

专项技能处理单一领域，如解除映射、补偿估算、证据、谈判、协议、仲裁或地方规则。它们接收编排器规范化后的案件数据，将结构化结果返回编排器。

专项技能不得：

- 自己创建第二套公开入口。
- 绕过 `classify_request` 或 `review_output`。
- 根据隐式聊天历史完成关键计算。
- 自动保存、上传或发送材料。
- 在 Markdown 中复制法律数值作为永久常量。
- 对胜诉、赔付或受理结果作保证。

## 新增或修改技能

1. 明确触发范围、输入、输出和拒绝范围。
2. 复用版本化案件字段和六状态。
3. 把确定性计算放入 `worker_rights_cn/tools/`。
4. 把来源放入来源数据，不把链接散落到提示词。
5. 添加正常、缺失事实、安全阻断和降级案例。
6. 运行该技能自己的 `scripts/run_*_cases.py`，再运行 guide、orchestrator 和安全核心回归。

用户材料中的所有案例必须是虚构、脱敏数据。不要放入真实样式的手机号或身份证号。

## 保存流程

当编排器返回 `save_confirmation` 时，入口先调用 `redaction_preview`，再显示绝对目标路径和 scope。只有 `confirm_save` 返回明确同意后才可进入 `CaseStore.save`。拒绝或缺少确认不应中断当前咨询。


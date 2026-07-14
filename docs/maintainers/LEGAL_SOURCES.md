# 法律来源与城市更新

法律结论必须回到官方来源、结构化 source card 和 article anchor。检索结果不能代替来源校验。

## source schema

全国来源位于 `references/source-currency.json`。每张 source card 至少维护：

- 稳定 `source_id`
- `title`、`authority`、`jurisdiction`、`source_type`
- `source_of_truth_url` 和必要的 `verification_urls`
- `publish_date`、`effective_date`
- `retrieved_at`、`current_as_of`
- `currency_status`
- `notes`

条文锚点采用 `SOURCE-ID#artN`。锚点摘要不能扩大条文含义。

城市数据位于 `skills/local-rules-adapter/references/city-rules.json`。来源卡还要维护 `official_host`、`source_status`、`allowed_uses`、`not_allowed_uses` 和 `values`。城市规则维护 aliases、rule_checks、required_facts、source_ids、output_flags 以及禁止作为最终数值的来源。

只有 `verified_final` 可以用于其明确授权用途的当地最终数值。`verified_candidate`、`verified_reference_only`、`verified_guardrail` 和 `local_verify` 不能被自动当成经济补偿最终上限。

## 全国来源更新

1. 从官方站点确认来源身份、现行状态和生效日期。
2. 检查官方 host allowlist，记录检索日期。
3. 更新 source card，再更新条文锚点。
4. 标记旧链接和替代链接，不静默覆盖来源历史。
5. 运行法律映射和来源时效校验。
6. 对受影响的计算、技能和用户文档做回归。

## 城市更新

1. 明确城市、用途和所需事实。工资、社保基数、统计工资和补偿上限不得混用。
2. 找到当前官方来源，记录发布、生效和检索日期。
3. 先添加 source card，再把 source_id 接入对应 rule_check。
4. 为允许用途和禁止用途各写回归。
5. 更新城市测试数据，验证别名、缺失来源、过期来源和禁止自动套用数值。
6. 没有最终来源时保留 `local_verify`；不要用猜测填空。

安全查看校验器接口：

```powershell
python plugins/worker-rights-cn/scripts/validate_source_currency.py --help
python plugins/worker-rights-cn/scripts/validate_legal_map.py --help
```

## 评审要求

来源 PR 应列出：受影响 source_id、官方链接、检索日期、状态变化、允许用途、禁止用途、受影响城市和测试结果。真实案件事实不得进入公共来源数据。


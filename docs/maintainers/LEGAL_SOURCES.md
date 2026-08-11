# 法律来源与城市更新

法律结论必须回到官方来源、结构化 source card 和 article anchor。检索结果不能代替来源校验。

## source schema

全国来源位于 `references/source-currency.json`。每张 source card 至少维护：

- 稳定 `source_id`
- `title`、`authority`、`jurisdiction`、`source_type`
- `source_of_truth_url` 和必要的 `verification_urls`
- `publish_date`、`effective_date`、`expiry_date`（没有已记录的固定失效日时为 `null`）
- `retrieved_at`、`current_as_of`
- `currency_status`
- `notes`

条文锚点采用 `SOURCE-ID#artN`。锚点摘要不能扩大条文含义。

顶层 `current_as_of` 表示全国来源卡的全量核验下限，必须等于 `national_sources` 中最早的 `current_as_of`。只复核部分卡片时，不得据此抬高全库日期。

同一 SOURCE-ID 在 `source-currency.json` 与 `legal-map.md` 中的标题、官方 URL 集合和 `retrieved_at` 必须一致，避免双份来源卡静默漂移。

计算规则中的来源卡可以只引用规范来源卡的部分 URL，但不得引入规范卡外链接，且 `retrieved_at` 必须与同名全国来源卡一致。

城市数据位于 `skills/local-rules-adapter/references/city-rules.json`。来源卡还要维护 `jurisdiction`、`effective_date`、`expiry_date`、`current_as_of`、`official_host`、`source_status`、`allowed_uses`、`not_allowed_uses` 和 `values`；其中两类用途边界必须各为非空字符串列表。城市规则维护 aliases、rule_checks、required_facts、source_ids、output_flags 以及禁止作为最终数值的来源。

默认复核期限为 366 天。`expiry_date: null` 只表示没有记录固定失效日，不代表永久有效；来源超过复核期限、尚未生效或已经失效时，校验器失败关闭，HTML 报告降级提示并列出 source ID。

`audit_date`、`retrieved_at` 和 `current_as_of` 不得晚于校验时的 `as_of`，避免未来日期掩盖来源过期或虚增核验时效。

官方来源链接必须使用 HTTPS，并通过官方 host allowlist 校验；仅主机名匹配但使用明文 HTTP 的链接不得通过。

host allowlist 条目本身必须是 `gov.cn` 或其子域，不得通过新增第三方主机绕过官方来源边界。

只有 `verified_final` 可以用于其明确授权用途的当地最终数值。`verified_candidate`、`verified_reference_only`、`verified_guardrail` 和 `local_verify` 不能被自动当成经济补偿最终上限。

标记为 `verified_final` 的地方来源卡必须至少保留一个正数值；空值或非对象 `values` 不能继续保持最终数值状态。

地方来源卡的 `publication_date` 必须是有效 ISO 日期；只有待核验的 `local_verify` 卡可以保留 `null`。

## 当前最低工资场景（2026-08-11 复核）

- 北京 `BJ-RSJ-MIN-WAGE-2025`：2025-09-01 起，全日制月标准 2540 元，非全日制小时标准 27.7 元，非全日制法定节假日小时标准 65.1 元。
- 上海 `SH-RSJ-MIN-WAGE-2025`：2025-07-01 起，全日制月标准 2740 元，非全日制小时标准 25 元。
- 深圳 `SZ-HRSS-MIN-WAGE-2025`：2025-03-01 起，全日制月标准 2520 元，非全日制小时标准 23.7 元。
- 广州 `GZ-RSJ-MIN-WAGE-2025`：2025-03-01 起，月标准 2500 元，非全日制小时标准 23.7 元。

这些卡片仅用于其标明的最低工资和工资差额初筛。使用前仍要确认工作地点、工资期间、全日制或非全日制、是否提供正常劳动以及当地计入或排除的工资组成；不得把最低工资数值用于经济补偿高工资上限。城市回归用例固定验证当前值，并用超过 366 天未复核的模拟日期验证 `local_verify` 降级和数值停用。

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

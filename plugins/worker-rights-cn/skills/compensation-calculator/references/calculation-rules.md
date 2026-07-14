# Calculation Rules

Retrieval date: 2026-06-16

This file provides baseline formulas for the calculator. Verify current official sources and local rules before using an amount as a final demand.

## Source Cards

```yaml
- title: "中华人民共和国劳动合同法"
  authority: "全国人民代表大会常务委员会"
  url: "https://flk.npc.gov.cn/detail2.html?MmM5MDlmZGQ2NzhiZjE3OTAxNjc4YmY3NGQ3MTA2YjM%3D="
  effective_date: "2013-07-01 for 2012 amendment"
  jurisdiction: "national"
  source_type: "law"
  reliability: "official"
  retrieved_at: "2026-06-16"
- title: "最高人民法院关于审理劳动争议案件适用法律问题的解释（二）"
  authority: "最高人民法院"
  url: "https://www.court.gov.cn/fabu/xiangqing/472691.html"
  effective_date: "2025-09-01"
  jurisdiction: "national"
  source_type: "judicial_interpretation"
  reliability: "official"
  retrieved_at: "2026-06-16"
- title: "中华人民共和国劳动合同法实施条例"
  authority: "国务院"
  url: "https://xzfg.moj.gov.cn/front/law/detail?LawID=284"
  effective_date: "2008-09-18"
  jurisdiction: "national"
  source_type: "administrative_regulation"
  reliability: "official"
  retrieved_at: "2026-06-16"
- title: "职工带薪年休假条例"
  authority: "国务院"
  url: "https://xzfg.moj.gov.cn/front/law/detail?LawID=208"
  effective_date: "2008-01-01"
  jurisdiction: "national"
  source_type: "administrative_regulation"
  reliability: "official"
  retrieved_at: "2026-06-16"
- title: "企业职工带薪年休假实施办法"
  authority: "人力资源和社会保障部"
  url: "https://www.moj.gov.cn/pub/sfbgw/flfggz/flfggzbmgz/200902/t20090209_144625.html"
  effective_date: "2008-09-18"
  jurisdiction: "national"
  source_type: "department_rule"
  reliability: "official"
  retrieved_at: "2026-06-16"
```

## Baseline Items

- `N`: economic compensation months. Full service year counts as 1 month wage; service over 6 months but less than 1 year counts as 1; service less than 6 months counts as 0.5.
- `N+1`: economic compensation plus substitute notice wage. Use only when the termination path supports substitute notice analysis.
- `2N`: unlawful termination compensation, generally 2 times the economic compensation baseline where the worker does not pursue or cannot obtain continued performance.
- High-wage cap: if the worker's average monthly wage exceeds 3 times the local average monthly wage, use the statutory cap and maximum years limit for the economic compensation baseline.
- Unsigned-contract double wage: estimate only when the no-written-contract period and exclusions are checked.
- Annual leave: default script estimates additional pay beyond normal wage with a 200% multiplier; verify local practice and whether normal wage has already been paid.
- Substitute notice wage: use the worker's previous month's wage when provided; if missing, the script falls back to average monthly wage and emits a warning.

## Inputs To Avoid Guessing

- Average monthly wage.
- Work start and end date.
- Local average monthly wage for cap analysis.
- Previous month's wage when claiming substitute notice wage.
- Number of unpaid wage months or amount.
- Number of unsigned-contract months owed.
- Unused annual leave days.
- Whether the worker caused or refused written-contract signing.

## Output Caution

Show formulas, not just totals. Label claims as `baseline`, `possible`, or `lawyer_check`.

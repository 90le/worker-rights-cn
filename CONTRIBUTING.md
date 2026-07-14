# 参与贡献

感谢你帮助 Worker Rights CN 为普通劳动者提供更清楚、更安全的信息整理工具。参与前请遵守 [行为准则](CODE_OF_CONDUCT.md)。

## 本地设置与测试

需要 Python 3.11+ 与 Node.js 18+。克隆仓库后，无需安装第三方 Python 依赖即可先运行公共门禁：

```powershell
python scripts/run_publication_readiness.py
python plugins/worker-rights-cn/scripts/run_documentation_cases.py --check
python plugins/worker-rights-cn/scripts/run_policy_cases.py
python plugins/worker-rights-cn/scripts/run_ci_matrix.py
```

修改某个 skill 时，还应运行其 `scripts/run_*_cases.py`。提交前至少运行与你变更直接相关的测试。

## 隐私与 Issue 规则

Issue、PR、测试夹具和日志中不得提交真实个案证据或敏感个人信息，包括真实姓名、身份证号、手机号、合同、聊天记录、录音、病历、银行卡信息、访问凭据及可反向识别当事人的组合信息。请使用明确标注为虚构、彻底脱敏的最小复现材料。安全漏洞请按 [安全政策](SECURITY.md) 私密报告。

## 提交与拉取请求

- 一个提交聚焦一个目的，使用清晰的祈使式提交说明；不要混入生成目录或无关格式化。
- PR 应说明问题、方案、影响范围、隐私风险和已执行的精确测试命令及结果，并关联对应 Issue（如有）。
- 新行为先添加失败测试，再做最小实现；维护向后兼容，必要的破坏性变化须在 PR 中明确迁移方式。
- 贡献按仓库 Apache License 2.0 授权；提交即表示你有权贡献该内容。

## 法律来源更新

更新法律、司法解释、部门规章或地方口径时，应优先引用可核验的官方来源，记录发布机关、标题、文号或来源 URL、生效/更新日期、适用地域和核验日期。区分全国规则与地方实践，不把草案、媒体解读或个案结论写成普遍规则。同步更新 `plugins/worker-rights-cn/references/source-currency.json`、相关测试及 `docs/maintainers/LEGAL_SOURCES.md`，并标注需要本地核验或律师复核的边界。

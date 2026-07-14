# 测试与发布

发布目标是让普通劳动者从 Codex 中直接使用，同时保持本地优先、来源可追溯和可删除。

## 测试矩阵

| 层 | 必测内容 | 主要门禁 |
| --- | --- | --- |
| 单元 | 金额、日期、六状态、脱敏、路径、SQLite 生命周期 | 对应 `run_*_cases.py` |
| 领域 | 裁员、辞退、欠薪、调岗、证据、协议、仲裁 | 各 skill case runner |
| 安全 | 伪造证据、威胁、雇主滥用、隐私泄漏、结果保证 | safety、privacy、output review |
| 宿主 | Codex 完整路径、adapter 清单、MCP、hook 和降级 | manifest、adapter、host smoke |
| 发布 | 全新加载、咨询、估算、显式保存、脱敏导出、删除证明 | package 和 end-to-end runner |

平台目标是 Windows 11、Ubuntu 24.04 和 macOS 上的 Codex。其他宿主按已声明支持的平台验证。

本地先运行 `python plugins/worker-rights-cn/scripts/run_ci_matrix.py` 校验 CI 矩阵。GitHub Actions 会运行 Windows、Ubuntu 24.04、macOS 与 Python 3.11/3.12 的六种组合并上传 JSON 报告；在远端工作流实际通过前，三平台状态必须记录为 `pending_external`。

CI 使用第二次 sparse checkout 从公开 `openai/plugins` 的固定 commit 取得完整 `plugins/plugin-eval` 树，并在入口缺失时失败。不得改回单文件下载或未固定的变量 URL。每个矩阵任务生成绑定 commit、系统、Python 版本、门禁清单和报告摘要哈希的 `ci-job.json`。发布验收只接受完整的 3×2 GitHub Actions 汇总证明，不接受手写的 `passed` 字符串。

Plugin Eval 的原始结果必须完整保留。当前 evaluator 会把插件内按需加载的 MCP 运行代码和法律参考资料全部计入 `deferred_cost_tokens`；在发布包合理最小闭包仍超过静态阈值时，只允许这一项以及尚未配置的三个公开 URL 项作为已审计例外，并且仅当 `trigger`、`invoke` 均未达到 heavy/excessive。任何其他 error 仍会阻断；公开 URL 例外同时由独立 `public_urls` 门禁保持阻断，不能借此放行公开发布。

## 发布门禁

候选发布必须满足：

- manifest 版本一致。
- runtime doctor 通过，Python 和 SQLite/FTS5 可用。
- 案件默认不保存，保存前显示绝对路径和 scope。
- 脱敏导出和删除证明通过。
- 法律来源与城市数据时效校验通过。
- 文档无坏链接、删除路径、真实样式标识符或危险 shell fence。
- release archive 不包含测试、缓存、日志、数据库、真实案例或调试资产。
- Codex 主流程通过；其他宿主失败只能作为明确降级，不能静默跳过。

安全的接口检查命令：

```powershell
python plugins/worker-rights-cn/scripts/build_release.py --help
python plugins/worker-rights-cn/scripts/sync_manifests.py --help
python plugins/worker-rights-cn/scripts/run_documentation_cases.py --check
```

完整本地门禁使用仓库内现有 runner：`run_manifest_cases.py`、`run_runtime_cases.py`、`run_package_cases.py`、`run_removed_product_path_cases.py`、领域/安全/privacy/storage runners 和 host smoke。它们是可执行测试，不是用户安装命令。

构建 archive 后，用隔离宿主目录验证安装、重复安装、0.1 升级、普通卸载保留案件与显式清除：

```text
python plugins/worker-rights-cn/scripts/run_install_cases.py --package dist/worker-rights-cn-0.2.0-development.zip
```

该命令只操作临时目录；不得把真实用户的 Codex 配置或案件目录传给测试 runner。OpenClaw 等真实次要宿主未安装时结果必须是 `pending_external`。

## 发布顺序

1. 确认工作树只含本次版本范围内的修改。
2. 同步清单并检查差异。
3. 运行来源、文档、隐私、安全、存储、MCP、宿主和 package 门禁。
4. 构建候选 archive，并再次扫描内容。
5. 在目标平台做全新加载和卸载/升级验证。
6. 生成机器可读结果和中文摘要，记录降级项与豁免理由。
7. 只有所有 P0/P1 门禁通过才允许发布。

## 公开仓库交付

1. 分别导出两个全新的公开快照，比较排序 inventory 及其 SHA-256；任何差异都阻断发布。
2. 在快照中重新扫描路径、文本、邮箱、凭据、私钥、数据库、报告和内部计划，确认只有 allowlist 内容。
3. 从已验证的快照创建新仓库和唯一一个公开初始 commit，不得携带本地开发历史；作者邮箱必须是 GitHub noreply 地址。
4. 在未登录浏览器中验证仓库、Pages、安装链接和政策链接；不能只依赖管理员会话。
5. 正式 Release 必须有真实远程 Windows/Ubuntu/macOS × Python 3.11/3.12 六组证据。任何 CI、Pages、匿名访问或安全门禁为红时，不得创建 tag 或 Release。

本项目不在文档更新任务中自动提交、推送或发布。发布者必须自行复核差异和签名材料。

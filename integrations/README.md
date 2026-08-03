# AI 宿主适配规划

Worker Rights CN 保持中国劳动权益场景专注，核心规则与不同 AI 宿主解耦。

## 支持方向

### Codex

主要入口。

用于：

- 劳动问题分析
- 证据整理
- 文书辅助

### Claude Code / OpenCode 等

复用核心能力，通过宿主提供的技能机制加载。

### MCP

未来提供标准工具接口，例如：

```
search_labor_rule
calculate_compensation
analyze_contract
generate_document
```

## 设计原则

- 中国劳动法场景优先
- 不输出确定性法律裁决
- 保持用户隐私
- 所有建议需要事实基础

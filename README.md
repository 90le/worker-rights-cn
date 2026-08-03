# Worker Rights CN

[简体中文](README.md) | [English](README.en.md) | [项目网站](https://90le.github.io/worker-rights-cn/)

# 中国劳动权益 AI Agent

一个开源的劳动权益助手，帮助劳动者用自然语言整理事实、证据、补偿估算和文书草稿。

支持 AI Agent 工作流：

- OpenAI Codex
- Claude Code
- OpenCode
- OpenClaw
- MCP 工具链

> 目标：让每个劳动者都拥有一个自己的劳动权益分析助手。

---

## 为什么做这个项目？

很多劳动者遇到：

- 突然被辞退，不知道是否违法
- 公司要求立即签解除协议
- 加班、欠薪、社保问题无法整理
- 不知道应该保存哪些证据
- 不知道如何准备劳动仲裁材料

Worker Rights CN 不替代律师，而是帮助你：

1. 理清事实
2. 整理证据
3. 理解可能权益
4. 准备沟通和材料

---

## 30 秒开始使用

安装 Codex 插件：

```bash
codex plugin marketplace add 90le/worker-rights-cn --ref main
codex plugin add worker-rights-cn@worker-rights-cn
```

然后直接描述你的情况：

```
我在广州工作，公司今天通知解除劳动合同。
请帮我整理：
1. 需要确认哪些事实
2. 应该保存哪些证据
3. 可能涉及哪些权益
4. 下一步应该怎么做
```

---

## 能解决什么问题？

### 被辞退 / 裁员

分析：

- 解除原因
- 通知方式
- 工资基数
- 可能补偿范围

### 欠薪

整理：

- 应发工资
- 实际支付
- 发薪记录
- 沟通证据

### 加班争议

帮助整理：

- 考勤
- 排班
- 邮件
- 工作记录

### 劳动仲裁准备

生成结构化草稿：

- 请求事项
- 事实经过
- 证据清单
- 法律依据

---

## AI Agent 架构

```text
用户描述问题
       |
       v
事实整理 Agent
       |
       +---- 证据分析
       |
       +---- 法规参考
       |
       +---- 金额估算
       |
       +---- 文书生成
       |
       v
行动建议
```

---

## 安全与边界

本项目：

- 不是律师
- 不是政府服务
- 不提供个案代理
- 不保证仲裁或诉讼结果

请始终核对：

- 最新法律法规
- 当地执行规则
- 专业法律意见

不要上传：

- 身份证
- 银行账号
- 未脱敏合同
- 真实个人隐私材料

---

## 兼容性

| 平台 | 状态 |
| --- | --- |
| Codex | ✅ 推荐入口 |
| Claude Code | ✅ 支持 |
| OpenCode | ✅ 适配 |
| OpenClaw | ✅ 支持 |
| MCP | ✅ 支持 |

---

## Roadmap

- [x] 劳动权益基础 Agent
- [x] 证据整理流程
- [x] 补偿估算辅助
- [x] 仲裁材料辅助

未来：

- [ ] 劳动法规知识库
- [ ] 劳动案例 RAG
- [ ] MCP Server
- [ ] Web AI 助手
- [ ] 更多 Agent 工作流

---

## 文档

- [快速开始](docs/%E5%BF%AB%E9%80%9F%E5%BC%80%E5%A7%8B.md)
- [裁员前后 72 小时](docs/%E8%A3%81%E5%91%98%E5%89%8D%E5%90%8E72%E5%B0%8F%E6%97%B6.md)
- [如何整理证据](docs/%E5%A6%82%E4%BD%95%E6%95%B4%E7%90%86%E8%AF%81%E6%8D%AE.md)
- [如何估算补偿](docs/%E5%A6%82%E4%BD%95%E4%BC%B0%E7%AE%97%E8%A1%A5%E5%81%BF.md)

---

## 贡献

欢迎贡献：

- 劳动法规整理
- 案例分析
- Agent 技能优化
- MCP 工具开发
- 文档改进

贡献前请阅读：

- [CONTRIBUTING.md](CONTRIBUTING.md)
- [SECURITY.md](SECURITY.md)

本项目采用 Apache-2.0 License。

# Worker Rights CN

[简体中文](README.md) | [Website](https://90le.github.io/worker-rights-cn/)

An open-source assistant that helps workers in mainland China organize facts, preserve evidence, estimate amounts, and prepare drafts. Codex is the canonical interface; other hosts are thin adapters.

## Safety boundary

This project is not a lawyer, law firm, government service, or case representative, and it does not guarantee outcomes. For imminent signing deadlines, workplace injury, expiring limitation periods, or threats to personal safety, preserve originals and seek timely help from the relevant local authority, union, or licensed counsel.

## 30-second Codex start

1. Run `codex plugin marketplace add 90le/worker-rights-cn --ref main`. This adds a marketplace source; it does not install the plugin directly.
2. Restart or refresh the Codex/ChatGPT desktop app and open **Plugins**.
3. Select the **Worker Rights CN** marketplace, then install **Worker Rights CN**.

First prompt: “Help me separate confirmed facts from missing information, identify what I should not do yet and what evidence to preserve today, then outline possible rights and next steps. Do not save or upload my materials.”

## Capabilities and limits

It can organize timelines, evidence, calculations, agreement risks, negotiation drafts, and arbitration drafts with source labels. It cannot sign, submit, upload, contact third parties, provide legal representation, or turn an estimate into an official decision.

## Privacy

Chats and case materials are not automatically saved, uploaded, or sent. Any persistence must disclose the absolute path and scope and require explicit consent. Review identifiers before export. See [PRIVACY.md](PRIVACY.md).

## Compatibility

Codex is primary. Claude Code, OpenCode, and OpenClaw reuse the same domain rules through thin adapters, but host capabilities such as hooks and MCP startup differ and are not claimed to be identical.

## Update, Uninstall, and purge

- Update the source with `codex plugin marketplace upgrade worker-rights-cn`, refresh the app, and update or reinstall through Plugins as offered.
- Uninstall through Plugins. Uninstalling the plugin does not automatically erase user data that you explicitly chose to save.
- For an explicit purge, first export anything needed, then delete the host-disclosed `worker-rights-cn` data directory and verify related indexes/audit records. Purge is irreversible and requires confirmation.

## Project

Author: 丘彬彬, Guangzhou, Guangdong. WeChat `binstudy` is for project and community collaboration only, not private case advice or representation. Contributions follow [CONTRIBUTING.md](CONTRIBUTING.md); security reports follow [SECURITY.md](SECURITY.md). Licensed under [Apache-2.0](LICENSE).

import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const pluginDir = dirname(fileURLToPath(import.meta.url));
const pluginRoot = join(pluginDir, "..", "..");
const hookRunner = join(pluginRoot, "scripts", "hook_runner.py");
const auditDbPath = join(pluginRoot, ".local", "worker-rights-opencode.db");

function sessionId(input) {
  return (
    input?.sessionID ||
    input?.session_id ||
    input?.session?.id ||
    input?.event?.sessionID ||
    "opencode-hook-session"
  );
}

const policyEvent = {
  user_prompt: "user_prompt_submit",
  pre_tool: "pre_tool_use",
  post_tool: "post_tool_use",
};

function canonicalEvent(event, payload) {
  return {
    host: "opencode",
    event,
    session_id: sessionId(payload),
    payload,
    timestamp: new Date().toISOString(),
  };
}

function evaluateHook(canonical) {
  const payload = {
    ...canonical,
    event: policyEvent[canonical.event] || canonical.event,
    audit: true,
    audit_db_path: auditDbPath,
    audit_session_id: canonical.session_id,
  };
  const result = spawnSync("python3", [hookRunner], {
    input: JSON.stringify(payload),
    encoding: "utf8",
  });
  if (![0, 2].includes(result.status)) {
    throw new Error(result.stderr || `worker-rights-cn hook runner failed with ${result.status}`);
  }
  return JSON.parse(result.stdout);
}

function reasonText(result) {
  if (!result.reasons?.length) {
    return "worker-rights-cn hook policy produced no matching risk rule.";
  }
  return result.reasons
    .map((reason) => `${reason.id}: ${reason.message || reason.id}`)
    .join("\n");
}

function warn(client, result) {
  const text = reasonText(result);
  if (client?.app?.log?.warn) {
    client.app.log.warn(text);
  }
}

export const WorkerRightsCn = async ({ client }) => {
  return {
    "tool.execute.before": async (input) => {
      const result = evaluateHook(canonicalEvent("pre_tool", {
        tool_name: input?.tool || input?.toolName || input?.name,
        tool_input: input?.args || input?.input || input,
        session_id: sessionId(input),
      }));
      if (result.decision === "block") {
        throw new Error(reasonText(result));
      }
      if (result.decision === "warn") {
        warn(client, result);
      }
    },
    "tool.execute.after": async (input) => {
      const result = evaluateHook(canonicalEvent("post_tool", {
        tool_name: input?.tool || input?.toolName || input?.name,
        tool_input: input?.args || input?.input,
        tool_result: input?.result || input?.output || input,
        session_id: sessionId(input),
      }));
      if (result.decision === "warn") {
        warn(client, result);
      }
    },
    event: async ({ event }) => {
      if (!["session.idle", "session.compact", "session.compacted"].includes(event?.type)) {
        return;
      }
      const result = evaluateHook(canonicalEvent(
        event.type === "session.idle" ? "stop" : "pre_compact",
        {
        payload: event,
        session_id: sessionId({ event }),
        },
      ));
      if (result.decision === "warn") {
        warn(client, result);
      }
    },
  };
};

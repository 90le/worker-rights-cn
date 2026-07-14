#!/usr/bin/env node
"use strict";

import { constants as osConstants } from "node:os";
import { posix, win32 } from "node:path";
import { realpathSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { spawn, spawnSync } from "node:child_process";


const PYTHON_ENV = "WORKER_RIGHTS_CN_PYTHON";
const PYTHON_PROBE_SENTINEL = "__worker_rights_cn_python_probe_7f3b1c9e__";


function pathApi(platform) {
  return platform === "win32" ? win32 : posix;
}


export function canonicalPath(
  filePath,
  { platform = process.platform, realpathSync: resolveRealpath = realpathSync.native } = {},
) {
  const paths = pathApi(platform);
  const absolute = paths.resolve(filePath);
  try {
    return paths.normalize(resolveRealpath(absolute));
  } catch {
    return paths.normalize(absolute);
  }
}


export function runtimePaths(launcherPath, platform = process.platform) {
  const paths = pathApi(platform);
  const pluginRoot = paths.resolve(paths.dirname(launcherPath), "..");
  return {
    pluginRoot,
    mcpServer: paths.resolve(pluginRoot, "scripts", "mcp_server.py"),
  };
}


export function isDirectEntry(modulePath, entryPath, options = {}) {
  if (!entryPath) {
    return false;
  }
  const platform = options.platform ?? process.platform;
  const moduleCanonical = canonicalPath(modulePath, options);
  const entryCanonical = canonicalPath(entryPath, options);
  return platform === "win32"
    ? moduleCanonical.toLowerCase() === entryCanonical.toLowerCase()
    : moduleCanonical === entryCanonical;
}


const MODULE_PATH = fileURLToPath(import.meta.url);
const LAUNCHER_PATH = canonicalPath(MODULE_PATH);
const { pluginRoot: PLUGIN_ROOT, mcpServer: MCP_SERVER } = runtimePaths(LAUNCHER_PATH);


export function pythonCandidates(env = process.env, platform = process.platform) {
  const candidates = [];
  if (env[PYTHON_ENV]) {
    candidates.push([env[PYTHON_ENV]]);
  }
  candidates.push(["python3"], ["python"]);
  if (platform === "win32") {
    candidates.push(["py", "-3"]);
  }
  return candidates;
}


function candidateWorks(candidate, env) {
  const [command, ...prefixArgs] = candidate;
  const probe = spawnSync(
    command,
    [
      ...prefixArgs,
      "-c",
      `import sys; print(${JSON.stringify(PYTHON_PROBE_SENTINEL)}) if sys.version_info >= (3, 10) else sys.exit(1)`,
    ],
    {
      stdio: ["ignore", "pipe", "ignore"],
      encoding: "utf8",
      env,
      timeout: 5000,
      windowsHide: true,
    },
  );
  return probe.status === 0 && probe.stdout.trim() === PYTHON_PROBE_SENTINEL;
}


export function resolvePython(env = process.env, platform = process.platform) {
  return pythonCandidates(env, platform).find((candidate) => candidateWorks(candidate, env)) ?? null;
}


export function forwardSignal(child, signal) {
  try {
    child.kill(signal);
    return true;
  } catch {
    return false;
  }
}


export function installSignalHandlers(processLike, child, onForwarded = () => {}) {
  const signalHandlers = new Map(
    ["SIGINT", "SIGTERM"].map((signal) => [
      signal,
      () => {
        if (forwardSignal(child, signal)) {
          onForwarded(signal);
        }
      },
    ]),
  );
  for (const [signal, handler] of signalHandlers) {
    processLike.once(signal, handler);
  }
  let active = true;
  return () => {
    if (!active) {
      return;
    }
    active = false;
    for (const [signal, handler] of signalHandlers) {
      processLike.removeListener(signal, handler);
    }
  };
}


export function exitCodeForClose(code, signal, forwardedSignal = null) {
  const effectiveSignal = signal ?? forwardedSignal;
  if (effectiveSignal) {
    return 128 + (osConstants.signals[effectiveSignal] ?? 1);
  }
  if (typeof code === "number") {
    return code;
  }
  return 1;
}


function spawnServer(candidate, { env, serverPath, pluginRoot, writeError }) {
  return new Promise((resolveExit) => {
    const [command, ...prefixArgs] = candidate;
    const child = spawn(command, [...prefixArgs, serverPath], {
      cwd: pluginRoot,
      env,
      stdio: "inherit",
      windowsHide: true,
    });
    let settled = false;
    let forwardedSignal = null;
    const cleanup = installSignalHandlers(process, child, (signal) => {
      forwardedSignal = signal;
    });

    child.once("error", (error) => {
      if (settled) {
        return;
      }
      settled = true;
      cleanup();
      writeError(`worker-rights-cn: failed to launch Python MCP server: ${error.message}`);
      resolveExit(1);
    });
    child.once("close", (code, signal) => {
      if (settled) {
        return;
      }
      settled = true;
      cleanup();
      resolveExit(exitCodeForClose(code, signal, forwardedSignal));
    });
  });
}


export async function launch({
  env = process.env,
  platform = process.platform,
  serverPath = MCP_SERVER,
  pluginRoot = PLUGIN_ROOT,
  writeError = (message) => console.error(message),
} = {}) {
  const candidate = resolvePython(env, platform);
  if (candidate === null) {
    writeError(
      `worker-rights-cn: Python 3.10+ was not found; install Python or set ${PYTHON_ENV} to a working interpreter.`,
    );
    return 1;
  }
  return spawnServer(candidate, { env, serverPath, pluginRoot, writeError });
}


if (isDirectEntry(MODULE_PATH, process.argv[1])) {
  process.exitCode = await launch();
}

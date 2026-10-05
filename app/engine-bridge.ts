/**
 * Dev/preview-only bridge: relays POST /__kosh/rpc to one long-lived `python -m engine.rpc`
 * child process (JSON lines over stdin/stdout). Used by `pnpm dev`, `pnpm preview` and the
 * Playwright E2E tests. The packaged desktop app does not use this — it talks to the engine
 * through a Tauri command (src-tauri/src/lib.rs) — and the app's CSP allows no HTTP at all.
 */
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import path from "node:path";
import { createInterface } from "node:readline";
import type { Connect, Plugin } from "vite";

const ROOT = path.resolve(__dirname, "..");

type Pending = (line: string) => void;

function startEngine() {
  const command = process.env.KOSH_ENGINE_CMD ?? "uv run --frozen python -m engine.rpc";
  const [program, ...args] = command.split(" ");
  const child: ChildProcessWithoutNullStreams = spawn(program, args, {
    cwd: ROOT,
    stdio: ["pipe", "pipe", "pipe"],
  });
  const pending: Pending[] = [];
  createInterface({ input: child.stdout }).on("line", (line) => pending.shift()?.(line));
  child.stderr.on("data", (chunk) => process.stderr.write(`[engine] ${chunk}`));
  child.on("exit", (code) => {
    const error = JSON.stringify({
      id: null,
      error: { type: "EngineExited", message: `engine process exited (code ${code})` },
    });
    pending.splice(0).forEach((resolve) => resolve(error));
  });
  const send = (request: string) =>
    new Promise<string>((resolve) => {
      pending.push(resolve);
      child.stdin.write(request.replace(/\n/g, " ") + "\n");
    });
  return { child, send };
}

function middleware(): Connect.NextHandleFunction {
  let engine: ReturnType<typeof startEngine> | undefined;
  return (req, res, next) => {
    if (req.url !== "/__kosh/rpc" || req.method !== "POST") return next();
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", async () => {
      if (!engine || engine.child.exitCode !== null) engine = startEngine();
      const line = await engine.send(body);
      res.setHeader("Content-Type", "application/json");
      res.end(line);
    });
  };
}

export function engineBridge(): Plugin {
  return {
    name: "kosh-engine-bridge",
    configureServer(server) {
      server.middlewares.use(middleware());
    },
    configurePreviewServer(server) {
      server.middlewares.use(middleware());
    },
  };
}

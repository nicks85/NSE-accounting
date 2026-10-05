/**
 * Engine client. In the desktop app requests go through the `engine_rpc` Tauri command (IPC to
 * a local engine process); in a browser (dev, preview, E2E) through the dev-server bridge at
 * /__kosh/rpc. Amounts are decimal strings end to end — never parsed into JS numbers for maths.
 */
export class EngineError extends Error {
  constructor(
    public readonly type: string,
    message: string,
  ) {
    super(message);
    this.name = "EngineError";
  }
}

type Response<T> = { id: number; result?: T; error?: { type: string; message: string } };

let nextId = 1;

function inTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

async function transport(request: string): Promise<string> {
  if (inTauri()) {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<string>("engine_rpc", { request });
  }
  const response = await fetch("/__kosh/rpc", { method: "POST", body: request });
  if (!response.ok) throw new EngineError("Bridge", `engine bridge returned ${response.status}`);
  return response.text();
}

export async function rpc<T>(method: string, params: object = {}): Promise<T> {
  const id = nextId++;
  const reply = JSON.parse(await transport(JSON.stringify({ id, method, params }))) as Response<T>;
  if (reply.error) throw new EngineError(reply.error.type, reply.error.message);
  return reply.result as T;
}

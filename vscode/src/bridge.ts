/**
 * JSON-RPC bridge client for communicating with the Python core.
 *
 * Spawns `dog serve` as a subprocess and communicates via
 * line-delimited JSON-RPC 2.0 over stdin/stdout.
 */

import * as cp from "child_process";
import * as vscode from "vscode";

interface JsonRpcRequest {
  jsonrpc: "2.0";
  id: number;
  method: string;
  params: Record<string, unknown>;
}

interface JsonRpcResponse {
  jsonrpc: "2.0";
  id: number;
  result?: Record<string, unknown>;
  error?: { code: number; message: string };
}

interface JsonRpcNotification {
  jsonrpc: "2.0";
  method: string;
  params: Record<string, unknown>;
}

type JsonRpcMessage = JsonRpcResponse | JsonRpcNotification;

export interface BridgeStatus {
  workspace: string;
  memory_count: number;
  instinct_count: number;
  provider: string;
  model: string;
}

export interface MemoryRecord {
  id: string;
  content: string;
  summary: string;
  memory_type: string;
  workspace: string;
  importance: number;
}

export interface InstinctDef {
  name: string;
  description: string;
  triggers: string[];
  prompt: string;
  retrieval_bias: string[];
}

export type BridgeAgentState =
  | "Ready"
  | "Thinking"
  | "RetrievingMemories"
  | "RunningTools"
  | "ExtractingMemories"
  | "Success"
  | "Error";

export class MemoryDogBridge {
  private process: cp.ChildProcess | null = null;
  private nextId = 1;
  private pending = new Map<number, (resp: JsonRpcResponse) => void>();
  private buffer = "";
  private onStatus: ((msg: string) => void) | null = null;
  private onToken: ((token: string) => void) | null = null;
  private onMemories: ((memories: MemoryRecord[]) => void) | null = null;
  private onState: ((state: BridgeAgentState, detail: string) => void) | null = null;
  private outputChannel: vscode.OutputChannel;

  constructor() {
    this.outputChannel = vscode.window.createOutputChannel("MemoryDog Bridge");
  }

  /** Start the Python bridge process. */
  async start(): Promise<void> {
    if (this.process) {
      return;
    }

    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || ".";

    // Try `dog serve` first, fall back to `python3 -m core.bridge`
    const cmd = "dog";
    const args = ["serve", "-w", workspaceRoot];

    this.outputChannel.appendLine(`Starting bridge: ${cmd} ${args.join(" ")}`);

    this.process = cp.spawn(cmd, args, {
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
    });

    this.process.stdout?.on("data", (data: Buffer) => {
      this.buffer += data.toString();
      this.processBuffer();
    });

    this.process.stderr?.on("data", (data: Buffer) => {
      this.outputChannel.appendLine(`[stderr] ${data.toString().trim()}`);
    });

    this.process.on("exit", (code) => {
      this.outputChannel.appendLine(`Bridge exited with code ${code}`);
      this.process = null;
      // Reject all pending
      for (const [id, resolve] of this.pending) {
        resolve({
          jsonrpc: "2.0",
          id,
          error: { code: -32000, message: "Bridge process exited" },
        });
      }
      this.pending.clear();
    });

    this.process.on("error", (err) => {
      this.outputChannel.appendLine(`Bridge error: ${err.message}`);
    });

    // Wait for it to be ready (ping)
    try {
      await this.call("ping", {});
      this.outputChannel.appendLine("Bridge ready");
    } catch (e) {
      this.outputChannel.appendLine(`Bridge ping failed: ${e}`);
      throw e;
    }
  }

  /** Stop the bridge process. */
  stop(): void {
    if (this.process) {
      this.process.stdin?.end();
      this.process.kill();
      this.process = null;
    }
  }

  /** Make a JSON-RPC call and return the result. */
  async call(method: string, params: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    if (!this.process || this.process.exitCode !== null) {
      throw new Error("Bridge not running");
    }

    const id = this.nextId++;
    const req: JsonRpcRequest = {
      jsonrpc: "2.0",
      id,
      method,
      params,
    };

    return new Promise((resolve, reject) => {
      this.pending.set(id, (resp: JsonRpcResponse) => {
        if (resp.error) {
          reject(new Error(resp.error.message));
        } else {
          resolve(resp.result || {});
        }
      });

      const line = JSON.stringify(req) + "\n";
      this.process!.stdin?.write(line);
    });
  }

  /** Stream a chat turn — returns final response text. */
  async chat(
    userInput: string,
    workspace: string,
    onStatus: (msg: string) => void,
    onToken: (token: string) => void,
    onState?: (state: BridgeAgentState, detail: string) => void,
    onMemories?: (memories: MemoryRecord[]) => void
  ): Promise<string> {
    this.onStatus = onStatus;
    this.onToken = onToken;
    this.onState = onState || null;
    this.onMemories = onMemories || null;

    try {
      const result = await this.call("chat", {
        user_input: userInput,
        workspace,
      });
      return (result.content as string) || "";
    } finally {
      this.onStatus = null;
      this.onToken = null;
      this.onState = null;
      this.onMemories = null;
    }
  }

  /** Get memories for a workspace. */
  async getMemories(workspace: string, query?: string, limit?: number): Promise<{ memories: MemoryRecord[]; total: number; error?: string }> {
    const result = await this.call("get_memories", {
      workspace,
      query: query || "",
      limit: limit || 20,
    });
    return result as unknown as { memories: MemoryRecord[]; total: number; error?: string };
  }

  /** Get loaded instincts. */
  async getInstincts(): Promise<{ instincts: InstinctDef[]; error?: string }> {
    const result = await this.call("get_instincts", {});
    return result as unknown as { instincts: InstinctDef[]; error?: string };
  }

  /** Get agent status. */
  async getStatus(workspace: string): Promise<BridgeStatus> {
    const result = await this.call("get_status", { workspace });
    return result as unknown as BridgeStatus;
  }

  /** Save config (API key, model). */
  async setConfig(apiKey?: string, model?: string): Promise<void> {
    const params: Record<string, unknown> = {};
    if (apiKey) { params.api_key = apiKey; }
    if (model) { params.model = model; }
    await this.call("set_config", params);
  }

  /** Reset conversation history for the current workspace. */
  async resetChat(workspace: string): Promise<void> {
    await this.call("reset_chat", { workspace });
  }

  /** Run health checks (API key, database, embeddings). */
  async checkHealth(): Promise<{ api_key: string; database: string; embedding: string; all_ok: boolean }> {
    const result = await this.call("check_health", {});
    return result as unknown as { api_key: string; database: string; embedding: string; all_ok: boolean };
  }

  /** Process buffered stdout lines. */
  private processBuffer(): void {
    const lines = this.buffer.split("\n");
    this.buffer = lines.pop() || ""; // keep incomplete line

    for (const line of lines) {
      if (!line.trim()) { continue; }
      try {
        const msg: JsonRpcMessage = JSON.parse(line);
        this.handleMessage(msg);
      } catch {
        this.outputChannel.appendLine(`[parse error] ${line}`);
      }
    }
  }

  private handleMessage(msg: JsonRpcMessage): void {
    // Notifications
    if (!("id" in msg) || msg.id === undefined) {
      const notif = msg as JsonRpcNotification;
      if (notif.method === "status" && this.onStatus) {
        this.onStatus(notif.params.message as string);
      } else if (notif.method === "token" && this.onToken) {
        this.onToken(notif.params.token as string);
      } else if (notif.method === "memories" && this.onMemories) {
        this.onMemories((notif.params.memories as MemoryRecord[]) || []);
      } else if (notif.method === "state" && this.onState) {
        this.onState(notif.params.state as BridgeAgentState, notif.params.detail as string);
      }
      return;
    }

    // Response to a pending request
    const resp = msg as JsonRpcResponse;
    const resolve = this.pending.get(resp.id);
    if (resolve) {
      this.pending.delete(resp.id);
      resolve(resp);
    }
  }

  get isRunning(): boolean {
    return this.process !== null && this.process.exitCode === null;
  }
}

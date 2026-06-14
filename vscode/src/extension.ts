import * as vscode from "vscode";
import * as path from "path";
import * as fs from "fs";
import { MemoryDogBridge } from "./bridge";
import { DOG_SPRITE_SHEET, SPRITE, DOG_STATES, FRAME_DURATIONS } from "./assets";

let bridge: MemoryDogBridge;
let statusBarItem: vscode.StatusBarItem;
let extensionContext: vscode.ExtensionContext;

// ═══════════════════════════════════════════════════════════
// Session architecture
// ═══════════════════════════════════════════════════════════

interface Session {
  id: string;
  name: string;
  workspace: string;
  panel?: vscode.WebviewPanel;
  createdAt: number;
  lastRetrievedMemoryIds: string[];
}

const sessions = new Map<string, Session>();
let activeSessionId: string | null = null;

function generateId(): string {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
}

function getWorkspaceName(): string {
  const folders = vscode.workspace.workspaceFolders;
  if (folders && folders.length > 0) return path.basename(folders[0].uri.fsPath);
  return ".";
}

// ═══════════════════════════════════════════════════════════
// Activation
// ═══════════════════════════════════════════════════════════

export function activate(context: vscode.ExtensionContext) {
  extensionContext = context;
  statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBarItem.text = "$(paw) MemoryDog";
  statusBarItem.tooltip = "MemoryDog";
  statusBarItem.command = "memorydog.newSession";
  statusBarItem.show();
  context.subscriptions.push(statusBarItem);

  bridge = new MemoryDogBridge();
  startBridgeAsync();

  // ── Sidebar: Sessions ──────────────────────────────────
  const sessionTree = new SessionTreeProvider(context.extensionUri);
  context.subscriptions.push(
    vscode.window.registerTreeDataProvider("memorydog.sessions", sessionTree)
  );

  // ── Sidebar: Memories ──────────────────────────────────
  const memoryProvider = new MemoryPanelProvider(context.extensionUri, bridge);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider("memorydog.memoryPanel", memoryProvider)
  );

  // ── Sidebar: Instincts ─────────────────────────────────
  const instinctProvider = new InstinctPanelProvider(context.extensionUri, bridge);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider("memorydog.instinctPanel", instinctProvider)
  );

  // ── Commands ───────────────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand("memorydog.newSession", () => openSession(context, generateId(), `Session ${sessions.size + 1}`, getWorkspaceName())),
    vscode.commands.registerCommand("memorydog.openSession", (sid?: string) => {
      if (sid) openSession(context, sid);
      else if (activeSessionId) openSession(context, activeSessionId);
    }),
    vscode.commands.registerCommand("memorydog.closeSession", (sid?: string) => closeSession(sid || activeSessionId)),
    vscode.commands.registerCommand("memorydog.renameSession", (sid?: string) => renameSession(sid || activeSessionId)),
    vscode.commands.registerCommand("memorydog.configure", configureApiKey),
    vscode.commands.registerCommand("memorydog.showQuickActions", showQuickActions),
    // Legacy
    vscode.commands.registerCommand("memorydog.start", () => vscode.commands.executeCommand("memorydog.newSession")),
    vscode.commands.registerCommand("memorydog.startChat", () => vscode.commands.executeCommand("memorydog.newSession")),
    vscode.commands.registerCommand("memorydog.showMemoryPanel", () => vscode.commands.executeCommand("memorydog.memoryPanel.focus")),
    vscode.commands.registerCommand("memorydog.showInstinctPanel", () => vscode.commands.executeCommand("memorydog.instinctPanel.focus")),
  );

  // ── Config listener ─────────────────────────────────────
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("memorydog.apiKey")) {
        const apiKey = vscode.workspace.getConfiguration("memorydog").get("apiKey") as string;
        if (apiKey && bridge.isRunning) bridge.setConfig(apiKey).catch(() => {});
      }
    })
  );

  // ── Periodic status refresh ──────────────────────────────
  refreshStatusBar();
  const interval = setInterval(refreshStatusBar, 30000);
  context.subscriptions.push({ dispose: () => clearInterval(interval) });

  // Restore sessions from workspace state
  restoreSessions(context, sessionTree);
}

export function deactivate() {
  bridge?.stop();
}

// ═══════════════════════════════════════════════════════════
// Session operations
// ═══════════════════════════════════════════════════════════

function openSession(context: vscode.ExtensionContext, sessionId: string, name?: string, workspace?: string) {
  let session = sessions.get(sessionId);
  if (!session) {
    const ws = workspace || getWorkspaceName();
    session = {
      id: sessionId,
      name: name || `Session ${sessions.size + 1}`,
      workspace: ws,
      createdAt: Date.now(),
      lastRetrievedMemoryIds: [],
    };
    sessions.set(sessionId, session);
  }

  // Create or reveal the editor tab
  if (session.panel) {
    session.panel.reveal(vscode.ViewColumn.Active);
  } else {
    const title = `${session.name}`;
    session.panel = vscode.window.createWebviewPanel(
      `memorydog.session.${sessionId}`,
      `MemoryDog: ${title}`,
      { viewColumn: vscode.ViewColumn.Active, preserveFocus: false },
      { enableScripts: true, retainContextWhenHidden: true }
    );

    session.panel.webview.html = readWebviewFile(context.extensionUri, session.panel.webview, "chat.html");

    // Send sprite configuration for the mascot
    const spriteUri = session.panel.webview.asWebviewUri(
      vscode.Uri.joinPath(context.extensionUri, DOG_SPRITE_SHEET)
    );
    session.panel.webview.postMessage({
      type: "sprite_config",
      spriteUrl: spriteUri.toString(),
      frameWidth: SPRITE.frameWidth,
      frameHeight: SPRITE.frameHeight,
      columns: SPRITE.columns,
      rows: SPRITE.rows,
      states: DOG_STATES,
      durations: FRAME_DURATIONS,
    });

    session.panel.webview.onDidReceiveMessage(async (msg) => {
      switch (msg.type) {
        case "ready":
          if (!bridge.isRunning) {
            session!.panel!.webview.postMessage({ type: "setup" });
          } else {
            const apiKey = vscode.workspace.getConfiguration("memorydog").get("apiKey") as string;
            session!.panel!.webview.postMessage({ type: apiKey ? "ready" : "setup" });
          }
          break;
        case "chat":
          await handleChat(session!, msg.text);
          break;
        case "setConfig":
          await handleSetConfig(session!, msg);
          break;
        case "showSetup":
          sendToChat(session!, { type: "setup" });
          break;
      }
    });

    session.panel.onDidDispose(() => {
      sessions.delete(sessionId);
      if (activeSessionId === sessionId) activeSessionId = null;
    });
  }

  activeSessionId = sessionId;
  persistSessions(context);
  refreshStatusBar();
}

function closeSession(sessionId: string | null) {
  if (!sessionId) return;
  const session = sessions.get(sessionId);
  if (!session) return;
  if (session.panel) {
    session.panel.dispose();
  } else {
    sessions.delete(sessionId);
  }
  if (activeSessionId === sessionId) {
    activeSessionId = sessions.size > 0 ? (sessions.keys().next().value || null) : null;
  }
}

async function renameSession(sessionId: string | null) {
  if (!sessionId) return;
  const session = sessions.get(sessionId);
  if (!session) return;
  const newName = await vscode.window.showInputBox({
    prompt: "Session name",
    value: session.name,
    placeHolder: "e.g. Bug Hunt, Refactoring, Planning",
  });
  if (newName && newName.trim()) {
    session.name = newName.trim();
    if (session.panel) {
      session.panel.title = `MemoryDog: ${session.name}`;
    }
  }
}

// ═══════════════════════════════════════════════════════════
// Session persistence in workspace state
// ═══════════════════════════════════════════════════════════

interface StoredSession {
  id: string;
  name: string;
  workspace: string;
  createdAt: number;
}

function persistSessions(context: vscode.ExtensionContext) {
  const data: StoredSession[] = [];
  for (const [, s] of sessions) {
    data.push({ id: s.id, name: s.name, workspace: s.workspace, createdAt: s.createdAt });
  }
  context.workspaceState.update("memorydog.sessions", data);
}

async function restoreSessions(context: vscode.ExtensionContext, tree: SessionTreeProvider) {
  const data = context.workspaceState.get<StoredSession[]>("memorydog.sessions");
  if (data && data.length > 0) {
    for (const s of data) {
      if (!sessions.has(s.id)) {
        sessions.set(s.id, {
          id: s.id, name: s.name, workspace: s.workspace,
          createdAt: s.createdAt, lastRetrievedMemoryIds: [],
        });
      }
    }
    tree.refresh();
  }
}

function listSessions(): Session[] {
  return Array.from(sessions.values()).sort((a, b) => b.createdAt - a.createdAt);
}

// ═══════════════════════════════════════════════════════════
// Chat handling (per session)
// ═══════════════════════════════════════════════════════════

function sendToChat(session: Session, msg: any) {
  session.panel?.webview.postMessage(msg);
}

async function handleSetConfig(session: Session, msg: any) {
  const apiKey = (msg.apiKey || "").trim();
  if (!apiKey) {
    sendToChat(session, { type: "setup_error", text: "Please enter an API key." });
    return;
  }
  if (apiKey.length < 8) {
    sendToChat(session, { type: "setup_error", text: "API key is too short." });
    return;
  }

  await vscode.workspace.getConfiguration("memorydog").update("apiKey", apiKey, vscode.ConfigurationTarget.Global);
  if (msg.model) {
    await vscode.workspace.getConfiguration("memorydog").update("model", msg.model, vscode.ConfigurationTarget.Global);
  }

  try { await bridge.setConfig(apiKey, msg.model); } catch { }
  if (!bridge.isRunning) {
    try {
      await bridge.start();
      await bridge.setConfig(apiKey, msg.model);
    } catch (e: any) {
      sendToChat(session, { type: "setup_error", text: `Failed to start bridge: ${e.message}` });
      return;
    }
  }

  // Auto-pull local model if needed (Ollama default)
  try {
    const info = await bridge.currentModel();
    if (info.provider_type === "ollama") {
      const ensure = await bridge.ensureModel();
      if (ensure.needed && ensure.pulling) {
        sendToChat(session, { type: "health", api_key: "ok", database: "ok", embedding: "ok" });
        sendToChat(session, { type: "setup_error", text: `Model "${info.model}" not found. Auto-pulling... This may take a few minutes. Check /status for progress.` });
        bridge.pullModel(info.model).catch(() => {});
        setTimeout(() => sendToChat(session, { type: "ready" }), 2000);
        refreshStatusBar();
        return;
      }
    }
  } catch { /* model check optional */ }

  // Validate connectivity
  try {
    const health = await bridge.checkHealth();
    const info = await bridge.currentModel().catch(() => ({ provider_type: "litellm" }));
    if (info.provider_type === "ollama") {
      // Ollama: only verify DB + embeddings, skip API key
      if (health.embedding !== "ok") {
        sendToChat(session, { type: "setup_error", text: "Ollama not running. Start with: ollama serve" });
        return;
      }
    } else {
      // Cloud provider: verify API key
      if (health.api_key !== "ok") {
        const reason = health.api_key === "rejected" ? "API key rejected."
          : health.api_key === "missing" ? "No API key configured."
          : health.api_key === "invalid" ? "API key too short."
          : `API key validation failed: ${health.api_key}`;
        sendToChat(session, { type: "setup_error", text: reason });
        return;
      }
    }
  } catch {}  // health check optional

  sendToChat(session, { type: "ready" });
  refreshStatusBar();
}

async function handleChat(session: Session, text: string) {
  const configuredKey = (vscode.workspace.getConfiguration("memorydog").get("apiKey") as string || "").trim();
  if (!configuredKey && !text.startsWith("/")) {
    sendToChat(session, { type: "setup_error", text: "Please enter an API key before chatting." });
    return;
  }

  // Slash command routing
  if (text.startsWith("/")) {
    await handleSlashCommand(session, text);
    return;
  }

  if (!bridge.isRunning) {
    try {
      await bridge.start();
      const apiKey = vscode.workspace.getConfiguration("memorydog").get("apiKey") as string;
      if (apiKey) await bridge.setConfig(apiKey);
    } catch (e: any) {
      sendToChat(session, { type: "error", text: `Bridge failed: ${e.message}` });
      return;
    }
  }

  try {
    const response = await bridge.chat(
      text,
      session.workspace,
      (statusMsg: string) => sendToChat(session, { type: "status", text: statusMsg }),
      (token: string) => {
        const cleaned = filterToken(token);
        if (cleaned) sendToChat(session, { type: "token", token: cleaned });
      },
      (state: string, detail: string) => sendToChat(session, { type: "state", state, detail }),
      (memories: any[]) => {
        session.lastRetrievedMemoryIds = memories.map((m: any) => m.id).filter(Boolean);
      }
    );

    if (response.startsWith("❌")) {
      sendToChat(session, { type: "error", text: response.replace(/^❌\s*/, "") });
    } else {
      sendToChat(session, { type: "response", content: response });
    }
    refreshStatusBar();
  } catch (e: any) {
    sendToChat(session, { type: "error", text: `Error: ${e.message}` });
  }
}

/** Strip tool-call XML/JSON artifacts from streaming tokens. */
function filterToken(token: string): string {
  let t = token;
  t = t.replace(/<invoke[^>]*\/>/g, "");
  t = t.replace(/<invoke[\s\S]*?<\/invoke>/g, "");
  t = t.replace(/<tool_calls>[\s\S]*?<\/tool_calls>/g, "");
  t = t.replace(/<parameter[\s\S]*?<\/parameter>/g, "");
  t = t.replace(/\{"name"\s*:\s*"[^"]*",\s*"arguments"\s*:\s*\{[^}]*\}\}/g, "");
  return t;
}

// ═══════════════════════════════════════════════════════════
// Slash Commands
// ═══════════════════════════════════════════════════════════

const branches = new Map<string, { parentId: string; createdAt: number; name: string }>();

async function handleSlashCommand(session: Session, text: string) {
  const parts = text.split(/\s+/);
  const cmd = parts[0].toLowerCase();
  const arg = parts.slice(1).join(" ");

  switch (cmd) {
    case "/help":
      sendToChat(session, { type: "response", content:
`**Commands**
- /model — Show current model
- /models — List installed models
- /model <name> — Switch active model
- /new — Create a new session
- /sessions — List sessions
- /session <name> — Switch to session
- /fork — Fork this conversation
- /branches — List forks
- /branch <n> — Switch to fork
- /clear — Clear conversation context
- /memory — Show memory statistics
- /status — Show extension status`
      });
      break;

    case "/model": {
      if (arg) {
        await bridge.setConfig(undefined, arg);
        sendToChat(session, { type: "response", content: `Model switched to **${arg}**. Send a new message to use it.` });
      } else {
        try {
          const info = await bridge.currentModel();
          sendToChat(session, { type: "response", content: `**Current model:** ${info.model} (provider: ${info.provider_type})` });
        } catch (e: any) {
          sendToChat(session, { type: "error", text: e.message });
        }
      }
      break;
    }

    case "/models": {
      try {
        const result = await bridge.listModels();
        const lines = result.models.map((m: any) =>
          `- **${m.name}** (${(m.size / 1e9).toFixed(1)}GB, modified ${new Date(m.modified).toLocaleDateString()})`
        );
        sendToChat(session, { type: "response", content: `**Installed models** (${result.count}):\n${lines.join("\n")}` });
      } catch (e: any) {
        sendToChat(session, { type: "error", text: `Failed to list models: ${e.message}` });
      }
      break;
    }

    case "/new": {
      const name = arg || `Session ${sessions.size + 1}`;
      vscode.commands.executeCommand("memorydog.newSession");
      break;
    }

    case "/sessions": {
      const lines: string[] = [];
      for (const [id, s] of sessions) {
        const marker = id === activeSessionId ? "●" : "○";
        lines.push(`${marker} **${s.name}** (${s.workspace})`);
      }
      sendToChat(session, { type: "response", content: `**Sessions** (${sessions.size}):\n${lines.join("\n")}` });
      break;
    }

    case "/session": {
      if (!arg) { sendToChat(session, { type: "error", text: "Usage: /session <name>" }); return; }
      const match = Array.from(sessions.values()).find(s => s.name.toLowerCase().includes(arg.toLowerCase()));
      if (match) {
        openSession(extensionContext, match.id);
      } else {
        sendToChat(session, { type: "error", text: `No session matching "${arg}"` });
      }
      break;
    }

    case "/fork": {
      const forkId = generateId();
      const forkName = `${session.name} (fork)`;
      branches.set(forkId, { parentId: session.id, createdAt: Date.now(), name: forkName });
      openSession(extensionContext, forkId, forkName, session.workspace);
      break;
    }

    case "/branches": {
      const lines: string[] = [];
      for (const [id, b] of branches) {
        if (b.parentId === session.id || id === session.id) {
          const parent = sessions.get(b.parentId);
          lines.push(`- **${b.name}** ← ${parent?.name || b.parentId}`);
        }
      }
      sendToChat(session, { type: "response", content: `**Branches**:\n${lines.join("\n") || "None"}` });
      break;
    }

    case "/branch": {
      if (!arg) { sendToChat(session, { type: "error", text: "Usage: /branch <name>" }); return; }
      const match = Array.from(branches.entries()).find(([_, b]) => b.name.toLowerCase().includes(arg.toLowerCase()));
      if (match) {
        openSession(extensionContext, match[0]);
      } else {
        sendToChat(session, { type: "error", text: `No branch matching "${arg}"` });
      }
      break;
    }

    case "/clear":
      vscode.commands.executeCommand("memorydog.closeSession", session.id);
      sendToChat(session, { type: "response", content: "Context cleared." });
      break;

    case "/memory": {
      try {
        const status = await bridge.getStatus(session.workspace);
        sendToChat(session, { type: "response", content: `**Memory:** ${status.memory_count} memories | **Instincts:** ${status.instinct_count} loaded` });
      } catch (e: any) {
        sendToChat(session, { type: "error", text: e.message });
      }
      break;
    }

    case "/status": {
      try {
        const info = await bridge.currentModel();
        const status = await bridge.getStatus(session.workspace);
        const lines = [
          `**Provider:** ${info.provider_type}`,
          `**Model:** ${info.model}`,
          `**Session:** ${session.name} (${sessions.size} total)`,
          `**Memories:** ${status.memory_count}`,
          `**Instincts:** ${status.instinct_count}`,
          `**Branches:** ${branches.size}`,
          `**Bridge:** ${bridge.isRunning ? "✅ running" : "❌ stopped"}`,
        ];
        sendToChat(session, { type: "response", content: lines.join("\n") });
      } catch (e: any) {
        sendToChat(session, { type: "error", text: e.message });
      }
      break;
    }

    default:
      sendToChat(session, { type: "error", text: `Unknown command: ${cmd}. Type /help for available commands.` });
  }
}

// ═══════════════════════════════════════════════════════════
// Session Tree Provider (sidebar)
// ═══════════════════════════════════════════════════════════

class SessionTreeProvider implements vscode.TreeDataProvider<vscode.TreeItem> {
  private _onDidChange = new vscode.EventEmitter<vscode.TreeItem | undefined>();
  readonly onDidChangeTreeData = this._onDidChange.event;

  constructor(private extensionUri: vscode.Uri) { }

  refresh() { this._onDidChange.fire(undefined); }

  getTreeItem(element: vscode.TreeItem): vscode.TreeItem {
    return element;
  }

  getChildren(): vscode.TreeItem[] {
    const items: vscode.TreeItem[] = [];

    // New Session button
    const newBtn = new vscode.TreeItem("$(add) New Session", vscode.TreeItemCollapsibleState.None);
    newBtn.command = { command: "memorydog.newSession", title: "New Session" };
    newBtn.iconPath = new vscode.ThemeIcon("add");
    items.push(newBtn);

    // Active sessions
    const sessionList = listSessions();
    for (const s of sessionList) {
      const isActive = s.id === activeSessionId;
      const label = isActive ? `$(circle-filled) ${s.name}` : `$(circle-outline) ${s.name}`;
      const item = new vscode.TreeItem(label, vscode.TreeItemCollapsibleState.None);
      item.tooltip = `Workspace: ${s.workspace}\nCreated: ${new Date(s.createdAt).toLocaleString()}\nClick to open`;
      item.contextValue = "session";
      item.id = s.id;
      item.command = { command: "memorydog.openSession", title: "Open", arguments: [s.id] };
      items.push(item);
    }

    return items;
  }
}

// ═══════════════════════════════════════════════════════════
// Quick Actions
// ═══════════════════════════════════════════════════════════

async function showQuickActions() {
  const sessionItems = listSessions().slice(0, 4).map(s => ({
    label: `$(comment-discussion) ${s.name}`,
    detail: s.workspace,
  }));

  const items: vscode.QuickPickItem[] = [
    { label: "$(add) New Session", detail: "Start a new conversation" },
    ...sessionItems,
    { label: "$(database) Memory Browser", detail: "Browse persistent memories" },
    { label: "$(zap) Instincts", detail: "View loaded instincts" },
    { label: "$(settings-gear) Configure", detail: "Set API key and model" },
  ];

  const choice = await vscode.window.showQuickPick(items, {
    placeHolder: "🐕 MemoryDog — what would you like to do?",
  });
  if (!choice) return;
  const label = choice.label;
  if (label.includes("New Session")) vscode.commands.executeCommand("memorydog.newSession");
  else if (label.includes("Memory Browser")) vscode.commands.executeCommand("memorydog.memoryPanel.focus");
  else if (label.includes("Instincts")) vscode.commands.executeCommand("memorydog.instinctPanel.focus");
  else if (label.includes("Configure")) vscode.commands.executeCommand("memorydog.configure");
  else {
    // It's a session — find by name
    const session = listSessions().find(s => label.includes(s.name));
    if (session) vscode.commands.executeCommand("memorydog.openSession", session.id);
  }
}

async function configureApiKey() {
  const apiKey = await vscode.window.showInputBox({
    prompt: "Enter your API key",
    password: true,
    placeHolder: "sk-...",
    value: vscode.workspace.getConfiguration("memorydog").get("apiKey") || "",
  });
  const trimmed = (apiKey || "").trim();
  if (apiKey !== undefined && trimmed) {
    await vscode.workspace.getConfiguration("memorydog").update("apiKey", trimmed, vscode.ConfigurationTarget.Global);
    try {
      await bridge.setConfig(trimmed);
      vscode.window.showInformationMessage("🐕 API key saved!");
    } catch {
      vscode.window.showInformationMessage("🐕 API key saved (restart bridge to apply)");
    }
  } else if (apiKey !== undefined) {
    vscode.window.showWarningMessage("API key cannot be empty.");
  }
}

// ═══════════════════════════════════════════════════════════
// Sidebar: Memory Panel (unchanged)
// ═══════════════════════════════════════════════════════════

class MemoryPanelProvider implements vscode.WebviewViewProvider {
  constructor(private readonly extensionUri: vscode.Uri, private readonly bridge: MemoryDogBridge) { }

  resolveWebviewView(webviewView: vscode.WebviewView) {
    webviewView.webview.options = { enableScripts: true };
    webviewView.webview.html = readWebviewFile(this.extensionUri, webviewView.webview, "memory.html");
    webviewView.webview.onDidReceiveMessage(async (msg) => {
      if (msg.type === "filter") await this.loadMemories(webviewView, msg.workspace || getWorkspaceName(), msg.workspace || undefined);
    });
    setTimeout(() => this.loadMemories(webviewView, getWorkspaceName()), 500);
  }

  private async loadMemories(webviewView: vscode.WebviewView, workspace: string, query?: string) {
    if (!bridge.isRunning) {
      await startBridgeForPanel(webviewView.webview);
      if (!bridge.isRunning) return;
    }
    try {
      const result = await bridge.getMemories(workspace, query);
      if (result.error) { webviewView.webview.postMessage({ type: "status", text: result.error }); return; }
      const session = activeSessionId ? sessions.get(activeSessionId) : null;
      webviewView.webview.postMessage({
        type: "memories", workspace, memories: result.memories || [], total: result.total,
        highlighted: session ? session.lastRetrievedMemoryIds : [],
      });
    } catch (e: any) {
      webviewView.webview.postMessage({ type: "status", text: `Error: ${e.message}` });
    }
  }
}

// ═══════════════════════════════════════════════════════════
// Sidebar: Instinct Panel (unchanged)
// ═══════════════════════════════════════════════════════════

class InstinctPanelProvider implements vscode.WebviewViewProvider {
  constructor(private readonly extensionUri: vscode.Uri, private readonly bridge: MemoryDogBridge) { }

  resolveWebviewView(webviewView: vscode.WebviewView) {
    webviewView.webview.options = { enableScripts: true };
    webviewView.webview.html = readWebviewFile(this.extensionUri, webviewView.webview, "instinct.html");
    setTimeout(() => this.loadInstincts(webviewView), 500);
  }

  private async loadInstincts(webviewView: vscode.WebviewView) {
    if (!bridge.isRunning) {
      await startBridgeForPanel(webviewView.webview);
      if (!bridge.isRunning) return;
    }
    try {
      const result = await bridge.getInstincts();
      if (result.error) { webviewView.webview.postMessage({ type: "status", text: result.error }); return; }
      const instincts = (result.instincts || []).map((i: any) => ({
        name: i.name, description: i.description, prompt: i.prompt || "",
        condition: (i.triggers || []).join(", "), priority: 0.5, active: false,
      }));
      webviewView.webview.postMessage({ type: "instincts", instincts });
    } catch (e: any) {
      webviewView.webview.postMessage({ type: "status", text: `Error: ${e.message}` });
    }
  }
}

async function startBridgeForPanel(webview: vscode.Webview) {
  try {
    await bridge.start();
    const apiKey = vscode.workspace.getConfiguration("memorydog").get("apiKey") as string;
    if (apiKey) await bridge.setConfig(apiKey);
    refreshStatusBar();
  } catch (e: any) {
    webview.postMessage({ type: "status", text: `Bridge not running: ${e.message}` });
  }
}

// ═══════════════════════════════════════════════════════════
// Shared helpers
// ═══════════════════════════════════════════════════════════

async function startBridgeAsync() {
  try {
    await bridge.start();
    const apiKey = vscode.workspace.getConfiguration("memorydog").get("apiKey") as string;
    if (apiKey) await bridge.setConfig(apiKey);
    refreshStatusBar();
  } catch (e) {
    statusBarItem.text = "$(error) MemoryDog";
    statusBarItem.tooltip = `Bridge failed to start: ${e}. Click to retry.`;
    console.error("Failed to start MemoryDog bridge:", e);
  }
}

async function refreshStatusBar() {
  if (!bridge.isRunning) {
    statusBarItem.text = "$(paw) MemoryDog";
    return;
  }
  try {
    const status = await bridge.getStatus(getWorkspaceName());
    const parts = ["$(paw)"];
    if (status.memory_count > 0) parts.push(`${status.memory_count} mem`);
    if (status.instinct_count > 0) parts.push(`${status.instinct_count} inst`);
    const sessionCount = sessions.size;
    if (sessionCount > 0) parts.push(`${sessionCount} session${sessionCount > 1 ? 's' : ''}`);
    statusBarItem.text = parts.join(" ");
    statusBarItem.tooltip = `MemoryDog\nWorkspace: ${status.workspace}\nModel: ${status.model}`;
  } catch {
    statusBarItem.text = "$(paw) MemoryDog";
  }
}

function readWebviewFile(extensionUri: vscode.Uri, webview: vscode.Webview, filename: string): string {
  const filePath = vscode.Uri.joinPath(extensionUri, "src", "webview", filename);
  let content = fs.readFileSync(filePath.fsPath, "utf-8");
  const nonce = getNonce();
  content = content.replace(
    "<head>",
    `<head>\n<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${webview.cspSource} data:; style-src ${webview.cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}';">`
  );
  content = content.replace(/<script>/g, `<script nonce="${nonce}">`);
  return content;
}

function getNonce(): string {
  let text = "";
  const possible = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  for (let i = 0; i < 32; i++) text += possible.charAt(Math.floor(Math.random() * possible.length));
  return text;
}

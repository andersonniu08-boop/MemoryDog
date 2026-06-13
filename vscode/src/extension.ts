import * as vscode from "vscode";
import * as path from "path";
import * as fs from "fs";
import { MemoryDogBridge } from "./bridge";

let bridge: MemoryDogBridge;
let statusBarItem: vscode.StatusBarItem;

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

  // Validate the API key against the provider before showing chat
  try {
    const health = await bridge.checkHealth();
    if (health.api_key !== "ok") {
      const reason = health.api_key === "rejected" ? "API key rejected by provider. Check your key."
        : health.api_key === "missing" ? "No API key configured."
        : health.api_key === "invalid" ? "API key too short."
        : `API key validation failed: ${health.api_key}`;
      sendToChat(session, { type: "setup_error", text: reason });
      return;
    }
  } catch (e: any) {
    // Health check timed out or bridge not responding — let user try anyway
    console.warn("Health check failed:", e.message);
  }

  sendToChat(session, { type: "ready" });
  refreshStatusBar();
}

async function handleChat(session: Session, text: string) {
  const configuredKey = (vscode.workspace.getConfiguration("memorydog").get("apiKey") as string || "").trim();
  if (!configuredKey) {
    sendToChat(session, { type: "setup_error", text: "Please enter an API key before chatting." });
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
      (token: string) => sendToChat(session, { type: "token", token }),
      (state: string, detail: string) => sendToChat(session, { type: "state", state, detail }),
      (memories: any[]) => {
        sendToChat(session, { type: "memories_retrieved", memories });
        session.lastRetrievedMemoryIds = memories.map((m: any) => m.id).filter(Boolean);
      }
    );

    sendToChat(session, { type: "response", content: response });
    refreshStatusBar();
  } catch (e: any) {
    sendToChat(session, { type: "error", text: `Error: ${e.message}` });
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
    `<head>\n<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${webview.cspSource}; style-src ${webview.cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}';">`
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

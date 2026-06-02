import * as vscode from "vscode";
import * as fs from "fs";

export function activate(context: vscode.ExtensionContext) {
  const statusBarItem = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Right,
    100
  );
  statusBarItem.text = "🐕 Ready";
  statusBarItem.tooltip = "MemoryDog";
  statusBarItem.command = "memorydog.showQuickActions";
  statusBarItem.show();
  context.subscriptions.push(statusBarItem);

  context.subscriptions.push(
    vscode.commands.registerCommand("memorydog.showQuickActions", async () => {
      const choice = await vscode.window.showQuickPick(
        [
          {
            label: "$(terminal) Start Chat",
            detail: "Open MemoryDog chat terminal with --mock",
            action: "chat",
          },
          {
            label: "$(database) Memory Browser",
            detail: "Browse persistent memories",
            action: "memory",
          },
          {
            label: "$(zap) Instincts",
            detail: "View loaded instincts",
            action: "instincts",
          },
          {
            label: "$(paw) Mascot",
            detail: "Show the animated dog",
            action: "dog",
          },
        ],
        { placeHolder: "🐕 MemoryDog — quick actions" }
      );
      if (!choice) { return; }
      const cmd: Record<string, string> = {
        chat: "memorydog.startChat",
        memory: "memorydog.memoryPanel.focus",
        instincts: "memorydog.instinctPanel.focus",
        dog: "memorydog.dogView.focus",
      };
      vscode.commands.executeCommand(cmd[choice.action]);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("memorydog.start", () => {
      const terminal = vscode.window.createTerminal("MemoryDog");
      terminal.show();
      terminal.sendText("dog chat");
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("memorydog.startChat", () => {
      const terminal = vscode.window.createTerminal("MemoryDog");
      terminal.show();
      terminal.sendText("dog chat --mock");
    })
  );

  const memoryProvider = new MemoryPanelProvider(context.extensionUri);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(
      "memorydog.memoryPanel",
      memoryProvider
    )
  );

  const instinctProvider = new InstinctPanelProvider(context.extensionUri);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(
      "memorydog.instinctPanel",
      instinctProvider
    )
  );

  const dogProvider = new DogViewProvider(context.extensionUri);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(
      "memorydog.dogView",
      dogProvider
    )
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("memorydog.showMemoryPanel", () => {
      vscode.commands.executeCommand("memorydog.memoryPanel.focus");
    }),
    vscode.commands.registerCommand("memorydog.showInstinctPanel", () => {
      vscode.commands.executeCommand("memorydog.instinctPanel.focus");
    }),
    vscode.commands.registerCommand("memorydog.showDogView", () => {
      vscode.commands.executeCommand("memorydog.dogView.focus");
    })
  );
}

export function deactivate() {}

function readWebviewFile(extensionUri: vscode.Uri, filename: string): string {
  const filePath = vscode.Uri.joinPath(
    extensionUri,
    "src",
    "webview",
    filename
  );
  return fs.readFileSync(filePath.fsPath, "utf-8");
}

class MemoryPanelProvider implements vscode.WebviewViewProvider {
  constructor(private readonly extensionUri: vscode.Uri) {}

  public resolveWebviewView(webviewView: vscode.WebviewView) {
    webviewView.webview.options = { enableScripts: true };
    webviewView.webview.html = readWebviewFile(
      this.extensionUri,
      "memory.html"
    );

    webviewView.webview.onDidReceiveMessage((msg) => {
      if (msg.type === "filter") {
        webviewView.webview.postMessage({
          type: "memories",
          workspace: msg.workspace,
          memories: [],
          total: 0,
        });
      }
    });

    setTimeout(() => {
      webviewView.webview.postMessage({
        type: "status",
        text: "Agent not started. Run MemoryDog: Start Chat to begin.",
      });
    }, 500);
  }
}

class InstinctPanelProvider implements vscode.WebviewViewProvider {
  constructor(private readonly extensionUri: vscode.Uri) {}

  public resolveWebviewView(webviewView: vscode.WebviewView) {
    webviewView.webview.options = { enableScripts: true };

    const html = readWebviewFile(this.extensionUri, "instinct.html");
    if (html) {
      webviewView.webview.html = html;
    } else {
      webviewView.webview.html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  :root { --bg: #1e1e1e; --fg: #d4d4d4; --accent: #007acc; --muted: #6a6a6a; }
  body { background: var(--bg); color: var(--fg); font-family: -apple-system, sans-serif; padding: 16px; margin: 0; }
  h3 { margin: 0 0 12px; font-weight: 600; font-size: 14px; }
  .instinct { margin-bottom: 10px; padding: 10px; background: #252526; border-left: 3px solid var(--accent); border-radius: 4px; }
  .instinct .name { font-weight: 600; font-size: 13px; }
  .instinct .desc { font-size: 12px; color: var(--muted); margin-top: 4px; }
</style>
</head>
<body>
  <h3>⚡ Instincts</h3>
  <div id="instincts"><em style="color:var(--muted)">No instincts loaded.</em></div>
</body>
</html>`;
    }
  }
}

class DogViewProvider implements vscode.WebviewViewProvider {
  constructor(private readonly extensionUri: vscode.Uri) {}

  public resolveWebviewView(webviewView: vscode.WebviewView) {
    webviewView.webview.options = { enableScripts: true };
    webviewView.webview.html = readWebviewFile(
      this.extensionUri,
      "dog.html"
    );

    webviewView.webview.onDidReceiveMessage((msg) => {
      if (msg.type === "setState") {
        webviewView.webview.postMessage({
          type: "state",
          state: msg.state,
        });
      }
    });

    setTimeout(() => {
      webviewView.webview.postMessage({
        type: "state",
        state: "idle",
      });
    }, 300);
  }
}

# MemoryDog — VS Code Extension

A memory-augmented coding agent with persistent memory, hybrid retrieval, and an animated dog mascot.

## Quick Install

1. Download `memorydog-0.1.0.vsix` from the releases page
2. Install in VS Code:

```bash
code --install-extension memorydog-0.1.0.vsix
```

Or install via VS Code UI:
1. Open VS Code
2. Go to Extensions view (Ctrl+Shift+X)
3. Click "..." → "Install from VSIX..."
4. Select the `.vsix` file

## Requirements

- **VS Code** 1.85.0 or later
- **Python 3.11+** with MemoryDog core installed (`pip install memorydog` or from source)
- **PostgreSQL 16 + pgvector** (or Docker for `docker compose up`)
- **Ollama** with `nomic-embed-text` (`ollama pull nomic-embed-text`)

## Features

- **🐕 Animated Dog Mascot** — Pure CSS dog with idle, sniffing, excited, and sleeping states
- **📚 Memory Browser** — Browse persistent memories in PostgreSQL, filter by workspace
- **⚡ Instinct Viewer** — View loaded instincts from `~/.memorydog/instincts.toml`
- **💬 Chat Terminal** — Launch the MemoryDog CLI agent in an integrated terminal

## Usage

1. Click the **🐕 MemoryDog** icon in the Activity Bar (left sidebar)
2. Use the **Mascot**, **Memories**, and **Instincts** panels
3. Click **"🐕 Ready"** in the status bar for quick actions
4. Select **"Start Chat"** to open the MemoryDog terminal

## Building from Source

```bash
cd vscode
npm install
npm run compile
npx @vscode/vsce package
code --install-extension memorydog-0.1.0.vsix
```

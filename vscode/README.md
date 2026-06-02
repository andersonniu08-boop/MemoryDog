# MemoryDog — VS Code Extension

A memory-augmented coding agent with an animated dog mascot.

## Features

- **Animated Dog Mascot** — Pure CSS dog with idle, sniffing, excited, and sleeping states
- **Memory Browser** — Browse persistent memories, filter by workspace
- **Instinct Viewer** — View loaded instincts with priority and activation status
- **Chat Terminal** — Launch the MemoryDog agent in an integrated terminal

## Dev Installation

```bash
cd vscode

# Install dependencies
npm install

# Compile TypeScript
npm run compile

# Package the extension (requires vsce)
npx @vscode/vsce package

# Or install from VSIX
code --install-extension memorydog-0.1.0.vsix
```

### Running via VS Code debugger

1. Open the `vscode/` directory in VS Code
2. Press `F5` to launch Extension Development Host
3. The MemoryDog icon appears in the Activity Bar

## Taking Screenshots

1. Launch the extension via `F5` (Extension Development Host)
2. Open the MemoryDog sidebar from the Activity Bar (dog icon)
3. Click the status bar "🐕 Ready" button for quick actions
4. Use the Memory Browser and Instincts panels
5. Take screenshots of each panel and the animated mascot

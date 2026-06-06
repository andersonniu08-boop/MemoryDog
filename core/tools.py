"""Tool registry and execution."""

import glob as glob_mod
import os
import re
import subprocess
from pathlib import Path

TOOL_DEFINITIONS = [
    {
        "name": "read",
        "description": "Read a file from the local filesystem",
        "parameters": {
            "type": "object",
            "properties": {
                "filePath": {"type": "string", "description": "Absolute path to the file"},
                "offset": {"type": "integer", "description": "Line number to start from"},
                "limit": {"type": "integer", "description": "Max lines to read"},
            },
            "required": ["filePath"],
        },
    },
    {
        "name": "write",
        "description": "Write content to a file (creates or overwrites)",
        "parameters": {
            "type": "object",
            "properties": {
                "filePath": {"type": "string", "description": "Absolute path to the file"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["filePath", "content"],
        },
    },
    {
        "name": "edit",
        "description": "Perform exact string replacement in a file",
        "parameters": {
            "type": "object",
            "properties": {
                "filePath": {"type": "string", "description": "Absolute path"},
                "oldString": {"type": "string", "description": "Text to replace"},
                "newString": {"type": "string", "description": "Replacement text"},
            },
            "required": ["filePath", "oldString", "newString"],
        },
    },
    {
        "name": "bash",
        "description": "Execute a shell command",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The command to run"},
                "workdir": {"type": "string", "description": "Working directory"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "glob",
        "description": "Find files matching a glob pattern",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern"},
                "path": {"type": "string", "description": "Directory to search in"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "grep",
        "description": "Search file contents with a regex pattern",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern"},
                "include": {"type": "string", "description": "File pattern filter"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "memory_search",
        "description": "Search the agent's persistent memory",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    },
]


def get_tool_definitions() -> list:
    """Return tool definitions in OpenAI-compatible format with type wrapper."""
    return [
        {
            "type": "function",
            "function": {k: v for k, v in t.items() if k != "type"},
        }
        for t in TOOL_DEFINITIONS
    ]


def _tool_read(params: dict) -> dict:
    path = Path(params["filePath"])
    if not path.exists():
        return {"success": False, "error": "File not found"}
    offset = params.get("offset", 1)
    limit = params.get("limit", 2000)
    lines = path.read_text().splitlines()
    page = lines[offset - 1 : offset - 1 + limit]
    return {"success": True, "content": "\n".join(page), "lines": len(page)}


def _tool_write(params: dict) -> dict:
    Path(params["filePath"]).write_text(params["content"])
    return {"success": True}


def _tool_edit(params: dict) -> dict:
    path = Path(params["filePath"])
    content = path.read_text()
    if params["oldString"] not in content:
        return {"success": False, "error": "oldString not found"}
    new_content = content.replace(params["oldString"], params["newString"], 1)
    path.write_text(new_content)
    return {"success": True}


def _tool_bash(params: dict) -> dict:
    workdir = params.get("workdir", ".")
    if not os.path.isdir(workdir):
        workdir = "."
    result = subprocess.run(
        params["command"],
        shell=True,
        capture_output=True,
        text=True,
        cwd=workdir,
        timeout=120,
    )
    return {
        "success": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.returncode,
    }


def _tool_glob(params: dict) -> dict:
    base = params.get("path", ".")
    matches = glob_mod.glob(params["pattern"], root_dir=base, recursive=True)
    return {"success": True, "matches": sorted(matches)[:100]}


def _tool_grep(params: dict) -> dict:
    results = []
    include = params.get("include", "*")
    base = Path(params.get("path", "."))
    pattern = re.compile(params["pattern"])
    for f in base.rglob(include):
        if f.is_file():
            try:
                for i, line in enumerate(f.read_text().splitlines(), 1):
                    if pattern.search(line):
                        results.append(f"{f}:{i}: {line.strip()[:120]}")
            except Exception:
                pass
    return {"success": True, "matches": results[:50]}


def _tool_memory_search(params: dict) -> dict:
    """Search MemoryDog's persistent memory.

    Full async retrieval is provided by agent_loop's _handle_memory_search
    when called through the agent loop. This is a fallback for direct calls.
    """
    return {
        "success": True,
        "memories": [],
        "message": "Use memory_search through the agent loop for full results",
    }


TOOL_REGISTRY = {
    "read": _tool_read,
    "write": _tool_write,
    "edit": _tool_edit,
    "bash": _tool_bash,
    "glob": _tool_glob,
    "grep": _tool_grep,
    "memory_search": _tool_memory_search,
}


def execute_tool(name: str, params: dict) -> dict:
    handler = TOOL_REGISTRY.get(name)
    if not handler:
        return {"success": False, "error": f"Unknown tool: {name}"}
    return handler(params)

# MemoryDog Benchmark Suite

A 4-task A/B comparison suite that measures whether persistent memory adds measurable value to a coding agent.

## What We Measure

| Metric | Why it matters |
|--------|---------------|
| **Task success** | Do verification checks pass across sessions? |
| **Context retention** | Does the agent remember API structure and decisions from earlier sessions? |
| **Preference adherence** | Does the agent follow user-stated style preferences in later sessions? |
| **History accumulation** | With memory ON, history grows across sessions; with memory OFF, each session starts fresh. |
| **Instinct activation** | Do the right instincts activate for each task? |

## The 4 Benchmark Tasks

### 1. API Evolution (3 sessions)
Build a REST API incrementally: basic CRUD → pagination → rate limiting. Tests whether the agent remembers the existing API structure when adding features.

### 2. Bug History (2 sessions)
Fix a race condition in session 1, then check whether the agent proactively finds the same bug pattern in a different file in session 2. Tests bug pattern transfer.

### 3. Style Rules (3 sessions)
User states a preference (dataclasses, not pydantic) in session 1. Sessions 2 and 3 request new models — verify the agent adheres to the stated preference.

### 4. Pattern Reuse (2 sessions)
Build a FastAPI CRUD service in one project, then build a similar service in a different project. Tests whether architectural patterns transfer across workspaces.

## Running Benchmarks

### Prerequisites

Activate the virtual environment:

```bash
source /tmp/mdog-venv/bin/activate
```

### Run all benchmarks via pytest

```bash
python -m pytest tests/benchmarks/ -v
```

### Run a single benchmark via the harness

```bash
python tests/benchmarks/harness.py --task api_evolution
python tests/benchmarks/harness.py --task bug_history --memory on
python tests/benchmarks/harness.py --task style_rules --memory off
```

Options:
- `--task` — one of: `api_evolution`, `bug_history`, `style_rules`, `pattern_reuse`
- `--memory` — `on`, `off`, or `both` (default: `both` — runs side-by-side)

### Output format

The harness prints a comparison table showing per-session and per-condition (memory ON vs OFF) results:

```
Session  Verify  Response  History  Instincts
1        0/3     YES       2        Bug Hunter
2        0/2     YES       4        none
3        0/3     YES       6        none
```

## How It Proves Memory Value

- **With memory ON:** `AgentState` is shared across all sessions of a task. History accumulates — later sessions have context about what was built before. The agent "remembers" the API structure, the bug pattern, the style preference, or the CRUD template.

- **With memory OFF:** Each session gets a fresh `AgentState`. No prior context. The agent starts from scratch every time.

With a real LLM provider plugged in, the verification shell commands (which check for actual code patterns in generated files) would show a clear win for memory ON. The harness uses `MockProvider` for fast, deterministic testing without API calls.

## Swapping in a Real Provider

To run benchmarks against a real LLM, replace `MockProvider` with `LiteLLMProvider` in the harness:

```python
# In _run_sessions():
from core.provider import LiteLLMProvider
provider = LiteLLMProvider(model="gpt-4o", api_key=os.environ["OPENAI_API_KEY"])
```

Then the shell verification commands will actually pass when the agent creates the files correctly.

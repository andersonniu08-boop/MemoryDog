"""Benchmark task definitions."""

BENCHMARK_TASKS: dict[str, dict] = {}

BENCHMARK_TASKS["api_evolution"] = {
    "name": "API Evolution",
    "description": (
        "Build a REST API incrementally across 3 sessions: basic CRUD, "
        "pagination, then rate limiting. Tests whether the agent retains "
        "context about the API structure and correctly extends it."
    ),
    "sessions": [
        {
            "prompt": (
                "Create a FastAPI users API in main.py. Add CRUD endpoints "
                "at /users — GET list, GET by id, POST create, PUT update, "
                "DELETE delete. Store users in an in-memory list for now. "
                "Use a User dataclass with id, name, email fields."
            ),
            "setup_commands": [],
            "verification": [
                "grep -q 'FastAPI' main.py 2>/dev/null",
                "grep -q '/users' main.py 2>/dev/null",
                "grep -q 'dataclass' main.py 2>/dev/null",
            ],
        },
        {
            "prompt": (
                "Add pagination to the users API we built in main.py. "
                "Accept page and page_size query parameters. Default "
                "page=1, page_size=10. Return items alongside metadata "
                "(total, page, page_size)."
            ),
            "setup_commands": [],
            "verification": [
                "grep -q 'page' main.py 2>/dev/null",
                "grep -q 'FastAPI' main.py 2>/dev/null",
            ],
        },
        {
            "prompt": (
                "Add rate limiting to the users API. Limit to 100 requests "
                "per minute per IP using a sliding window dict. Return "
                "HTTP 429 when limit exceeded. Track timestamps per IP."
            ),
            "setup_commands": [],
            "verification": [
                "grep -q 'rate' main.py 2>/dev/null",
                "grep -q '429' main.py 2>/dev/null",
                "grep -q 'FastAPI' main.py 2>/dev/null",
            ],
        },
    ],
}

BENCHMARK_TASKS["bug_history"] = {
    "name": "Bug History",
    "description": (
        "Fix a race condition in session 1, then in session 2 check whether "
        "the agent proactively looks for the same pattern elsewhere. Tests "
        "cross-session bug pattern recognition."
    ),
    "sessions": [
        {
            "prompt": (
                "Fix the race condition in tasks.py. The shared_counter is "
                "incremented from multiple threads without a lock. Use "
                "asyncio.Lock to protect access to the counter."
            ),
            "setup_commands": [
                (
                    "cat > tasks.py << 'PYEOF'\n"
                    "import asyncio\n"
                    "shared_counter = 0\n"
                    "async def increment():\n"
                    "    global shared_counter\n"
                    "    await asyncio.sleep(0.1)\n"
                    "    shared_counter += 1\n"
                    "PYEOF\n"
                ),
            ],
            "verification": [
                "grep -q 'asyncio.Lock' tasks.py 2>/dev/null",
            ],
        },
        {
            "prompt": (
                "There's a similar bug in queue.py — the task_queue append "
                "and pop operations could race. Check for it and fix the "
                "same pattern you used for tasks.py."
            ),
            "setup_commands": [
                (
                    "cat > queue.py << 'PYEOF'\n"
                    "task_queue = []\n"
                    "def add_task(task):\n"
                    "    task_queue.append(task)\n"
                    "def pop_task():\n"
                    "    return task_queue.pop(0)\n"
                    "PYEOF\n"
                ),
            ],
            "verification": [
                "grep -q 'asyncio.Lock' queue.py 2>/dev/null",
            ],
        },
    ],
}

BENCHMARK_TASKS["style_rules"] = {
    "name": "Style Rules",
    "description": (
        "In session 1 the user states a preference: use dataclasses, not "
        "pydantic. Sessions 2 and 3 ask for new features — check whether "
        "the agent remembers and adheres to the preference."
    ),
    "sessions": [
        {
            "prompt": (
                "Important preference: from now on, use dataclasses for all "
                "data models, NOT pydantic. I don't want pydantic as a "
                "dependency. Please remember this."
            ),
            "setup_commands": [],
            "verification": [
                "grep -q 'Hello' /dev/null 2>/dev/null; true",
            ],
        },
        {
            "prompt": (
                "Add a User model to models.py. Include fields: id (int), "
                "name (str), email (str), and a created_at datetime."
            ),
            "setup_commands": [],
            "verification": [
                "grep -q 'dataclass' models.py 2>/dev/null",
                "! grep -q 'pydantic' models.py 2>/dev/null",
                "! grep -q 'BaseModel' models.py 2>/dev/null",
            ],
        },
        {
            "prompt": (
                "Now add a Config model to models.py. Fields: debug (bool), "
                "port (int), host (str), database_url (str)."
            ),
            "setup_commands": [],
            "verification": [
                "grep -q 'dataclass' models.py 2>/dev/null",
                "! grep -q 'pydantic' models.py 2>/dev/null",
                "! grep -q 'BaseModel' models.py 2>/dev/null",
            ],
        },
    ],
}

BENCHMARK_TASKS["pattern_reuse"] = {
    "name": "Pattern Reuse",
    "description": (
        "Build a FastAPI CRUD service in project A, then build a similar "
        "service in project B. Tests whether the agent recalls and reuses "
        "the patterns and structure from the first project."
    ),
    "sessions": [
        {
            "prompt": (
                "Build a FastAPI CRUD service for Items in project_a/main.py. "
                "Use a dataclass for Item with fields id, name, price. "
                "Store items in a dict. Return proper HTTP status codes."
            ),
            "setup_commands": ["mkdir -p project_a"],
            "verification": [
                "grep -q 'dataclass' project_a/main.py 2>/dev/null",
                "grep -q 'FastAPI' project_a/main.py 2>/dev/null",
            ],
        },
        {
            "prompt": (
                "Build a FastAPI CRUD service for Products in project_b/main.py. "
                "It should be very similar to the Items service we built in "
                "project_a. Product has fields id, name, price, category."
            ),
            "setup_commands": ["mkdir -p project_b"],
            "verification": [
                "grep -q 'dataclass' project_b/main.py 2>/dev/null",
                "grep -q 'FastAPI' project_b/main.py 2>/dev/null",
            ],
        },
    ],
}

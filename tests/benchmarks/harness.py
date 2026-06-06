"""Benchmark harness — runs task sessions with MockProvider, records results."""

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.agent_loop import AgentState, run_turn
from core.provider import MockProvider
from tests.benchmarks.tasks import BENCHMARK_TASKS


async def _run_sessions(
    sessions: list[dict],
    memory_on: bool,
    workspace_dir: str,
) -> list[dict]:
    provider = MockProvider()
    state = AgentState(workspace=os.path.basename(os.path.abspath(workspace_dir)))
    results: list[dict] = []

    for i, session in enumerate(sessions):
        result = {
            "session": i + 1,
            "prompt": session["prompt"],
            "response": "",
            "verification_passed": 0,
            "verification_total": len(session.get("verification", [])),
            "instincts": [],
            "history_turns": 0,
            "exception": None,
        }

        ws = Path(workspace_dir)
        for cmd in session.get("setup_commands", []):
            try:
                subprocess.run(cmd, shell=True, cwd=str(ws), capture_output=True, timeout=30)
            except Exception as exc:
                result["exception"] = f"setup failed: {exc}"
                results.append(result)
                continue

        try:
            resp = await run_turn(provider, state, session["prompt"])
            result["response"] = (resp or "")[:300]
        except Exception as exc:
            result["exception"] = f"run_turn failed: {exc}"
            results.append(result)
            continue

        result["instincts"] = list(state.active_instincts)
        result["history_turns"] = len([m for m in state.history if m.role in ("user", "assistant")])

        passed = 0
        for vcmd in session.get("verification", []):
            try:
                r = subprocess.run(vcmd, shell=True, cwd=str(ws), capture_output=True, timeout=10)
                if r.returncode == 0:
                    passed += 1
            except Exception:
                pass
        result["verification_passed"] = passed

        if not memory_on:
            state = AgentState(workspace=os.path.basename(os.path.abspath(workspace_dir)))

        results.append(result)

    return results


def _print_table(task_name: str, results_on: list[dict], results_off: list[dict]) -> None:
    print(f"\n{'=' * 75}")
    print(f"  Benchmark: {task_name}")
    print(f"{'=' * 75}")
    print(
        f"  {'Sess':<6}"
        f" {'Memory ON — Verify':>20}"
        f" {'Resp':>8}"
        f" {'History':>9}"
        f" {'Instincts':>12}"
        f"  |  "
        f" {'Memory OFF — Verify':>20}"
        f" {'Resp':>8}"
        f" {'History':>9}"
        f" {'Instincts':>12}"
    )
    print(f"  {'-' * 70}")

    for r_on, r_off in zip(results_on, results_off):
        on_verify = f"{r_on['verification_passed']}/{r_on['verification_total']}"
        off_verify = f"{r_off['verification_passed']}/{r_off['verification_total']}"
        on_resp = "YES" if r_on["response"] else "NO"
        off_resp = "YES" if r_off["response"] else "NO"
        on_history = r_on["history_turns"]
        off_history = r_off["history_turns"]
        on_instincts = ", ".join(r_on["instincts"]) if r_on["instincts"] else "none"
        off_instincts = ", ".join(r_off["instincts"]) if r_off["instincts"] else "none"

        print(
            f"  {r_on['session']:<6}"
            f" {on_verify:>20}"
            f" {on_resp:>8}"
            f" {on_history:>9}"
            f" {on_instincts:>12}"
            f"  |  "
            f" {off_verify:>20}"
            f" {off_resp:>8}"
            f" {off_history:>9}"
            f" {off_instincts:>12}"
        )

    on_total = sum(r["verification_passed"] for r in results_on)
    off_total = sum(r["verification_passed"] for r in results_off)
    on_max = sum(r["verification_total"] for r in results_on)
    off_max = sum(r["verification_total"] for r in results_off)

    print(f"  {'-' * 70}")
    print(
        f"  {'TOTAL':<6}"
        f" {f'{on_total}/{on_max}':>20}"
        f" {'':>8} {'':>9} {'':>12}"
        f"  |  "
        f" {f'{off_total}/{off_max}':>20}"
    )
    print(f"{'=' * 75}\n")


async def run_benchmark(task_name: str) -> tuple[list[dict], list[dict]]:
    if task_name not in BENCHMARK_TASKS:
        valid = ", ".join(BENCHMARK_TASKS)
        msg = f"Unknown task: {task_name}. Valid tasks: {valid}"
        raise ValueError(msg)

    task = BENCHMARK_TASKS[task_name]
    sessions = task["sessions"]

    with tempfile.TemporaryDirectory() as tmpdir_on:
        results_on = await _run_sessions(sessions, memory_on=True, workspace_dir=tmpdir_on)

    with tempfile.TemporaryDirectory() as tmpdir_off:
        results_off = await _run_sessions(sessions, memory_on=False, workspace_dir=tmpdir_off)

    _print_table(task["name"], results_on, results_off)
    return results_on, results_off


async def run_single(task_name: str, memory_on: bool) -> list[dict]:
    task = BENCHMARK_TASKS[task_name]
    sessions = task["sessions"]
    label = "ON" if memory_on else "OFF"
    state_type = "SHARED across sessions" if memory_on else "FRESH per session"

    with tempfile.TemporaryDirectory() as tmpdir:
        print(f"\nMemory {label} — {state_type}")
        results = await _run_sessions(sessions, memory_on=memory_on, workspace_dir=tmpdir)
        for r in results:
            status = "PASS" if r["exception"] is None and r["response"] else "FAIL"
            print(
                f"  Session {r['session']}: {status}  "
                f"verify={r['verification_passed']}/{r['verification_total']}  "
                f"history={r['history_turns']}  "
                f"instincts={r['instincts'] or 'none'}"
            )
        return results


async def _cli_main(args: argparse.Namespace) -> None:
    if args.memory in ("both", None):
        await run_benchmark(args.task)
    else:
        await run_single(args.task, args.memory == "on")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MemoryDog Benchmark Harness — compare memory ON vs OFF",
    )
    parser.add_argument(
        "--task",
        required=True,
        choices=list(BENCHMARK_TASKS),
        help="Which benchmark task to run",
    )
    parser.add_argument(
        "--memory",
        choices=["on", "off", "both"],
        default="both",
        help="Run with memory on, off, or both (default: both)",
    )
    args = parser.parse_args()
    asyncio.run(_cli_main(args))


def test_api_evolution():
    results_on, results_off = asyncio.run(run_benchmark("api_evolution"))
    assert len(results_on) == 3
    assert len(results_off) == 3
    assert all(r["response"] for r in results_on)
    assert all(r["response"] for r in results_off)


def test_bug_history():
    results_on, results_off = asyncio.run(run_benchmark("bug_history"))
    assert len(results_on) == 2
    assert len(results_off) == 2
    assert all(r["response"] for r in results_on)
    assert all(r["response"] for r in results_off)


def test_style_rules():
    results_on, results_off = asyncio.run(run_benchmark("style_rules"))
    assert len(results_on) == 3
    assert len(results_off) == 3
    assert all(r["response"] for r in results_on)
    assert all(r["response"] for r in results_off)


def test_pattern_reuse():
    results_on, results_off = asyncio.run(run_benchmark("pattern_reuse"))
    assert len(results_on) == 2
    assert len(results_off) == 2
    assert all(r["response"] for r in results_on)
    assert all(r["response"] for r in results_off)


if __name__ == "__main__":
    main()

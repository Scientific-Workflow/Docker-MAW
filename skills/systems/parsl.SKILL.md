---
name: systems/parsl
description: >
  Parsl parallel scripting library — codegen and execution reference for MAW workflows.
  Covers the exact config, @python_app rules, main() structure, run_workflow.sh template,
  common pitfalls, and AppFuture API. Load whenever generating or debugging Parsl workflow
  code, or when stack_decision.workflow_system is "parsl".
---

# Parsl — System Skill

Parsl orchestrates workflow steps as asynchronous Python functions. In MAW, it manages simulation and analysis tasks on a single node using local worker processes.

---

## When to Use This Skill

Load when `stack_decision.workflow_system == "parsl"` (the default). Covers the exact config and API patterns required — follow them precisely, they encode hard-won fixes.

---

## Exact Config — copy this block exactly, no extra kwargs

```python
from parsl.config import Config
from parsl.executors import HighThroughputExecutor
from parsl.providers import LocalProvider
import parsl

config = Config(
    run_dir='/tmp/parsl_runinfo',
    executors=[
        HighThroughputExecutor(
            label="local_htex",
            cores_per_worker=1,
            provider=LocalProvider(
                min_blocks=1,
                max_blocks=1,
                init_blocks=1,
            ),
        )
    ],
    strategy="none",
)
parsl.load(config)
```

**Critical notes:**
- `run_dir='/tmp/parsl_runinfo'` MUST be present — `/tmp` is always a writable native path. Without it, Parsl may try to write its certificates directory to an NFS-mounted or permission-restricted path, causing `OSError: The certificates directory must be private`.
- Do NOT add `max_workers`, `max_workers_per_node`, or any kwargs not shown — they do not exist in recent Parsl versions and cause `TypeError` at startup.
- `strategy="none"` prevents auto-scaling issues in local mode.

---

## @python_app Rules

```python
@python_app
def my_step(arg1, arg2):
    import os      # ALL imports inside function body
    import shutil  # workers don't share main namespace
    return result  # must be picklable
```

- All imports go inside the function body — workers run in a separate process
- No closures over mutable outer state — only pass serializable arguments
- `.result()` only in `main()`, never inside another app
- Return values must be picklable (strings, ints, simple dicts, paths)

---

## main() Structure

```python
def main():
    import argparse, os, parsl
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--work-dir", default="./work/run0")
    args = parser.parse_args()

    result1 = step_one(args.data_dir, args.work_dir).result()
    result2 = step_two(result1, os.path.join(args.work_dir, "results.csv")).result()
    print(f"Done: {result2}")
    parsl.clear()

if __name__ == "__main__":
    main()
```

Call `parsl.clear()` as the final statement in `main()` to release workers cleanly.

---

## run_workflow.sh Template

```bash
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${1:-$SCRIPT_DIR/../data}"
WORK_DIR="${2:-$SCRIPT_DIR/../work/run_default}"
echo "=== Starting workflow ==="
echo "  data-dir: $DATA_DIR"
echo "  work-dir: $WORK_DIR"
conda run -n maw_sandbox --no-capture-output \
  python3 "$SCRIPT_DIR/workflow.py" \
  --data-dir "$DATA_DIR" \
  --work-dir "$WORK_DIR"
```

- NEVER use `$(pwd)` — always use `BASH_SOURCE` resolution
- Pass ONLY `--data-dir` and `--work-dir`
- This script is for manual runs — the executor in agent.py calls workflow.py directly via `conda run`

---

## Common Pitfalls

| Pitfall | Rule |
|---|---|
| Imports at module level inside `@python_app` | All imports must be inside the function body |
| Calling `.result()` inside an app | Deadlocks the worker — only call in `main()` |
| Extra kwargs in `HighThroughputExecutor` | Causes `TypeError` — copy config exactly |
| `strategy` not set to `"none"` | Can cause auto-scaling issues in local mode |
| `parsl.load()` called twice without `parsl.clear()` | Raises `NoDataFlowKernelError` |
| `WorkerLost` error | Worker process crashed — check stderr for the actual exception |
| Omitting `run_dir='/tmp/parsl_runinfo'` | `OSError: certificates directory must be private` |

---

## Lifecycle

```python
parsl.load(config)      # call once at startup
future = my_app(args)   # submit task — returns immediately
value = future.result() # blocks until done
parsl.clear()           # call at end of main() to release workers
```

---

## AppFuture API

```python
future = my_app(args)
value = future.result()     # block and get return value
exc   = future.exception()  # returns exception or None
done  = future.done()       # bool, non-blocking
```

---

## Molecular Nucleation — Parsl Chaining Example

How to apply `@python_app` and chain the molecular nucleation workflow tasks. The function bodies live in `use_cases/molecular_nucleation/codegen`.

```python
from parsl.app.app import python_app

@python_app
def run_lammps(input_script, data_dir, work_dir):
    # ... body from use-case skill ...

@python_app
def analyze_with_ovito(frames_dir, output_csv):
    # ... body from use-case skill ...

@python_app
def render_frames(frames_dir, work_dir):
    # ... body from use-case skill ...

@python_app
def render_nucleation_timeseries(csv_path, output_png):
    # ... body from use-case skill ...


def main():
    import argparse, os, parsl
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--work-dir", default="./work/run0")
    args = parser.parse_args()

    input_script = os.path.join(args.data_dir, "<input_script_filename>")  # exact name from planner tasks

    frames_dir = run_lammps(input_script, args.data_dir, args.work_dir).result()
    output_csv = analyze_with_ovito(frames_dir, os.path.join(args.work_dir, "results.csv")).result()
    render_frames(frames_dir, args.work_dir).result()
    render_nucleation_timeseries(
        output_csv,
        os.path.join(args.work_dir, "renders", "nucleation_timeseries.png")
    ).result()
    print(f"Results: {output_csv}")
    parsl.clear()
```

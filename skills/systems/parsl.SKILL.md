---
name: systems/parsl
description: >
  Parsl parallel scripting library reference for MAW workflows. Covers @python_app/@bash_app
  rules, HighThroughputExecutor config for local single-node execution, common pitfalls,
  and the exact config skeleton used in this project.
---

# Parsl — System Skill

Parsl orchestrates workflow steps as asynchronous Python functions. In MAW, it manages the LAMMPS simulation and OVITO analysis as parallel tasks on a single local node inside Docker.

---

## When to Use This Skill

Load when generating or debugging Parsl workflow code. Covers the exact config and API patterns used in this project.

---

## Overview

Parsl wraps Python functions with `@python_app` to run them as managed tasks. Tasks return `AppFuture` objects; call `.result()` to block and get the value. Workers run in separate processes — imports must be inside function bodies.

---

## Exact Config for This Project

```python
from parsl.config import Config
from parsl.executors import HighThroughputExecutor
from parsl.providers import LocalProvider
import parsl

config = Config(
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

**CRITICAL:** Do NOT add `max_workers`, `max_workers_per_node`, or any kwargs not shown. They cause `TypeError` in recent Parsl versions.

---

## @python_app Rules

```python
@python_app
def my_step(arg1, arg2):
    import os      # ALL imports inside function body
    import shutil  # workers don't share main namespace
    return result  # must be picklable
```

- All imports go inside the function body
- No closures over mutable outer state
- `.result()` only in `main()`, never inside another app
- Return values must be picklable (strings, ints, simple dicts)

---

## Common Pitfalls

| Pitfall | Rule |
|---|---|
| Imports at module level inside @python_app | All imports must be inside the function body |
| Calling `.result()` inside an app | Deadlocks the worker — only call in main() |
| Extra kwargs in HighThroughputExecutor | Causes TypeError — copy config exactly |
| `strategy` not set to `"none"` | Can cause auto-scaling issues in local mode |
| `parsl.load()` called twice without `parsl.clear()` | Raises NoDataFlowKernelError |
| `WorkerLost` error | Worker process crashed — check stderr for the actual exception |

---

## Lifecycle

```python
parsl.load(config)      # call once at startup
# ... submit tasks ...
future = my_app(args).result()   # blocks until done
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

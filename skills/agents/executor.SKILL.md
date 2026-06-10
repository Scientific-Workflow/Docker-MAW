---
name: agents/executor
description: >
  Base skill for the executor agent. Covers how to run the generated workflow inside
  the maw_sandbox conda environment, capture output, and return results to the orchestrator.
---

# Executor Agent — Base Skill

Runs `workflow.py` inside the `maw_sandbox` conda environment and captures stdout/stderr for orchestrator review.

---

## When to Use This Skill

Loaded on every executor() call.

---

## Overview

The executor calls `conda run -n maw_sandbox python3 builds/workflow.py` with `--data-dir` and `--work-dir` pointing to real host paths. Each run gets a unique timestamped directory (`work/run_YYYYMMDD_HHMMSS/`) so results are never overwritten. Returns the full stdout/stderr and exit code in `execution_output`.

No containers, no Docker, no volume mounts.

---

## Step-by-Step Workflow

### Step 1: Resolve conda env name

```python
conda_env = state.get("conda_env_name") or "maw_sandbox"
```

### Step 2: Stage data files

Copy user-selected data files from `data/` into `work/<run_id>/data/`. This is the path passed as `--data-dir` to the workflow.

### Step 3: Build subprocess env

```python
run_env = os.environ.copy()
for k, v in state["stack_decision"]["env_vars"].items():
    run_env[k] = v
```

Pass `env=run_env` to `subprocess.run`. Do NOT use `-e` flags — there is no Docker.

### Step 4: Run the workflow

```python
cmd = [
    conda_bin, "run", "-n", conda_env, "--no-capture-output",
    "python3", workflow_py,
    "--data-dir", data_staging,   # absolute host path
    "--work-dir", work_dir,       # absolute host path
]
proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, env=run_env)
```

`workflow_py` is the absolute host path to `builds/workflow.py`.
`data_staging` is the absolute host path to the per-run staged data directory.
`work_dir` is the absolute host path to the per-run output directory.

### Step 5: Capture and return output

Capture stdout + stderr. Prepend exit code line. Return in `execution_output`.

---

## Key Rules and Constraints

- Pass ONLY `--data-dir` and `--work-dir` to workflow.py
- `--data-dir` and `--work-dir` are REAL HOST PATHS — no `/app/` container paths
- env_vars go into the subprocess env dict, not as CLI flags
- Exit code 0 = success; anything else = failure
- `--no-capture-output` is required on `conda run` so stdout/stderr flow normally to the calling process

---

## Notes

- `EXECUTOR_PROMPT = "TODO"` — executor uses hardcoded logic, not an LLM
- Ownership: Ivy owns executor()

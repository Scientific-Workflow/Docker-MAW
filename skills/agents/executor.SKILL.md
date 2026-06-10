---
name: agents/executor
description: >
  Base skill for the executor agent. Covers how to run the generated workflow inside
  the sandbox Docker container, capture output, and return results to the orchestrator.
---

# Executor Agent — Base Skill

Runs `workflow.py` inside the sandbox Docker container and captures stdout/stderr for orchestrator review.

---

## When to Use This Skill

Loaded on every executor() call.

---

## Overview

The executor builds a `docker run` command that mounts the repo root at `/app` and runs `python3 /app/builds/workflow.py` with `--data-dir` and `--work-dir` arguments. Each run gets a unique timestamped directory (`work/run_YYYYMMDD_HHMMSS/`) so results are never overwritten. Returns the full stdout/stderr and exit code in `execution_output`.

---

## Step-by-Step Workflow

### Step 1: Resolve image tag

```python
image_tag = state.get("image_tag") or "maw-sandbox:latest"
```

Use `or` not `.get(..., default)` — the key exists but may be empty string.

### Step 2: Build and run docker command

Read environment variables from `state["stack_decision"]["env_vars"]` and pass each as a `-e KEY=VALUE` flag. Do not hardcode env vars — they come from what the planner specified.

```bash
docker run --rm \
  -v "$HOST_REPO_PATH":/app \
  -w /app/builds \
  -e KEY1=VALUE1 \
  -e KEY2=VALUE2 \
  <image_tag> \
  python3 /app/builds/workflow.py \
  --data-dir /app/data \
  --work-dir /app/work/run0
```

`HOST_REPO_PATH` must be the real host path (not the container path `/app`). Read from `os.environ.get("HOST_REPO_PATH", ...)`.

### Step 3: Capture and return output

Capture stdout + stderr combined. Append exit code line. Return in `execution_output`.

---

## Key Rules and Constraints

- Pass ONLY `--data-dir` and `--work-dir` to workflow.py — no `--input-script` or other args
- Always use `HOST_REPO_PATH` for the volume mount source (not `os.getcwd()`)
- Exit code 0 = success; anything else = failure

---

## Notes

- `EXECUTOR_PROMPT = "TODO"` — executor currently uses hardcoded logic, not an LLM
- Ownership: Ivy owns executor()

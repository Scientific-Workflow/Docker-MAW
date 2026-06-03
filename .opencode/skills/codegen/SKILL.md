---
name: codegen
description: Use when writing, reviewing, or debugging code for the MAW Docker multi-agent workflow system in this repo. Covers the codegen agent prompt, CoderOutput schema, file generation conventions, Parsl executor config, LAMMPS/OVITO API rules, Docker launcher script, two-phase installer, orchestrator feedback handling, code ownership rules, and how all agent nodes fit together. Trigger keywords: codegen, workflow.py, run_workflow.sh, CoderOutput, CODEGEN_PROMPT, codegen agent, generate workflow code, code generation agent, Docker-MAW.
---

# MAW Docker Codegen Agent — AI Coding Reference

This skill covers **how to write, extend, and debug code** for the `codegen` node and its surrounding
multi-agent system in `agent.py` of the Docker-MAW repo. Everything here is derived from the actual
prompts, architecture, and ownership rules in this repository.

---

## 0. Code Ownership — READ FIRST

> This repo has strict ownership rules defined in `PROJECT_CONTEXT.md`. Violating them risks
> overwriting a teammate's work.

| Owner | Owns |
|---|---|
| **Jacob** | `ORCHESTRATOR_SYSTEM_PROMPT`, `PLANNER_PROMPT`, `INSTALLER_PROMPT`, `OrchestratorOutput`, `PlannerOutput`, `InstallerOutput`, `orchestrator()`, `planner()`, `installer()`, graph wiring, `__main__` entrypoint, `AgentState` (shared) |
| **Ivy** | `CODEGEN_PROMPT`, `EXECUTOR_PROMPT`, `CodeFile`, `CoderOutput`, `codegen()`, `executor()`, `builds/workflow.py`, `builds/run_workflow.sh` |

**Before editing any section of `agent.py`, identify the owner.**

- Touching Ivy's section → issue `*** OWNERSHIP WARNING — IVY'S DOMAIN ***` and confirm first.
- Touching Jacob's section → issue `*** OWNERSHIP WARNING — JACOB'S DOMAIN ***` and confirm first.
- Adding/removing `AgentState` fields → issue `*** SHARED STATE WARNING ***` — affects all agents.
- `EXECUTOR_PROMPT = "TODO"` is intentional — do not fill it in unless Ivy asks.

---

## 1. System Architecture

```
orchestrator  ──▶  planner    (reads PDF → literature_findings, stack_decision, tasks)
              ──▶  installer  phase 1: generates Dockerfile (pending approval)
                              phase 2: runs docker build (after orchestrator approves)
              ──▶  codegen    (generates workflow.py + run_workflow.sh into builds/)
              ──▶  executor   (runs workflow.py inside the Docker sandbox container)
              ──▶  END
```

All nodes speak through `AgentState`. The orchestrator decides routing after every node return.
Nodes never call each other directly.

**Two-phase installer** — unique to Docker-MAW:
- Phase 1: installer generates the Dockerfile and returns with `current_step="installer_dockerfile_pending_approval"`.
- Orchestrator reviews the Dockerfile and sets `dockerfile_approved=True` to proceed, or gives feedback to regenerate.
- Phase 2: installer runs `docker build` only after explicit approval.

**Key orchestrator rules:**
- `RULE: ALWAYS ROUTE TO EXECUTOR AFTER THE CODE GEN AGENT` — never re-route codegen→codegen.
- Only route back to codegen after executor if execution actually failed (real stderr, real exit code).
- Never fabricate runtime errors — let actual execution output drive routing.

---

## 2. AgentState Fields

```python
class AgentState(TypedDict):
    messages:              Annotated[Sequence[BaseMessage], add_messages]
    goal:                  str
    pdf_path:              str
    literature_findings:   list[str]          # set by planner
    stack_decision:        list[str]          # set by planner
    tasks:                 list[str]          # set by planner
    code_output:           Annotated[list[str], operator.add]  # accumulated
    execution_output:      Annotated[list[str], operator.add]  # accumulated
    dockerfile:            str                # set by installer phase 1
    dockerfile_approved:   bool               # set by orchestrator to trigger phase 2
    image_tag:             str                # set by installer phase 2 ("maw-sandbox:latest")
    current_step:          str
    orchestrator_feedback: str
    next:                  str
    planner_revisions:     int
    installer_revisions:   int
    codegen_revisions:     int
    executor_revisions:    int
```

**Key rules:**
- `code_output` and `execution_output` use `operator.add` — they **accumulate** (list of strings).
- `dockerfile_approved` is `False` by default; only the orchestrator sets it `True`.
- `image_tag` defaults to `"maw-sandbox:latest"` if not set.

---

## 3. Pydantic Schemas

### CoderOutput (Ivy's domain)

```python
class CodeFile(BaseModel):
    filename: str    # e.g. "workflow.py"
    content:  str    # full file content

class CoderOutput(BaseModel):
    files:           list[CodeFile]
    needs_more_info: bool = False
    missing_info:    str  = ""
```

### InstallerOutput (Jacob's domain)

```python
class InstallerOutput(BaseModel):
    dockerfile_content: str   # note: "dockerfile_content", NOT "def_file_content"
```

### OrchestratorOutput (Jacob's domain)

```python
class OrchestratorOutput(BaseModel):
    reasoning:           str
    next:                Literal["planner", "installer", "codegen", "executor", "end"]
    feedback:            str
    dockerfile_approved: bool = False   # True ONLY when approving a pending Dockerfile
```

---

## 4. JSON Parsing — `_invoke_structured()`

Docker-MAW uses a **custom helper** instead of `.with_structured_output()`:

```python
def _invoke_structured(llm, schema, messages):
    """Tolerates preamble text before the JSON block."""
    import json as _json, re as _re
    response = llm.invoke(messages)
    text = response.content if hasattr(response, "content") else str(response)
    match = _re.search(r'\{.*\}', text, _re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in model response:\n{text[:500]}")
    return schema.model_validate(_json.loads(match.group(0)))
```

**Usage pattern across all nodes:**
```python
result: CoderOutput = _invoke_structured(coder_llm, CoderOutput, [
    SystemMessage(content=CODEGEN_PROMPT),
    HumanMessage(content=context),
])
```

This replaces `.with_structured_output(..., method="json_mode")` from the old Apptainer repo.

---

## 5. CODEGEN_PROMPT — What the LLM Is Asked to Generate

The codegen node sends `CODEGEN_PROMPT` as the system message. It must produce **exactly two files**.

### FILE 1: `workflow.py`

#### 5.1 Parsl Config (exact — do not add extra kwargs)

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

**CRITICAL:** Do NOT add `max_workers`, `max_workers_per_node`, or any other kwargs not shown above.
They do not exist in recent Parsl versions and will cause a `TypeError` at startup.

#### 5.2 `run_lammps` — @python_app

```python
@python_app
def run_lammps(input_script, data_dir, work_dir):
    import os, shutil
    from lammps import lammps          # Python wheel — NOT subprocess

    os.makedirs(work_dir, exist_ok=True)
    for fname in ["data.init", "AW.tersoff"]:
        shutil.copy(os.path.join(data_dir, fname), work_dir)
    shutil.copy(input_script, work_dir)

    # CRITICAL: chdir into work_dir so LAMMPS dumps to frames/ relative to CWD
    os.chdir(work_dir)
    os.makedirs("frames", exist_ok=True)

    lmp = lammps(cmdargs=["-screen", "none"])
    lmp.file(os.path.join(work_dir, os.path.basename(input_script)))
    lmp.close()
    return os.path.join(work_dir, "frames")
```

**Critical rules:**
- All imports inside the function body (`@python_app` rule).
- `os.chdir(work_dir)` BEFORE running LAMMPS — the input script dumps to `frames/` relative to CWD.
- Use `from lammps import lammps` — never call the lammps binary as a subprocess.

#### 5.3 `analyze_with_ovito` — @python_app

```python
@python_app
def analyze_with_ovito(frames_dir, output_csv):
    import os, csv
    from ovito.io import import_file
    from ovito.modifiers import IdentifyDiamondModifier

    pipeline = import_file(os.path.join(frames_dir, "step.*.lammpstrj"))
    pipeline.modifiers.append(IdentifyDiamondModifier())

    with open(output_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["frame", "timestep", "cubic_diamond_count", "hexagonal_diamond_count"])
        for frame_idx in range(pipeline.source.num_frames):
            data = pipeline.compute(frame_idx)
            struct = data.particles["Structure Type"]
            cubic = int((struct == 1).sum())
            hexag = int((struct == 3).sum())
            writer.writerow([frame_idx, data.attributes.get("Timestep", frame_idx), cubic, hexag])

    return output_csv
```

**Critical rules:**
- Use `ovito.io.import_file()` — do NOT call `Pipeline()` constructor directly.
- Import modifiers from `ovito.modifiers`.
- All imports inside the function body.
- Never call `.result()` inside an app.

#### 5.4 `main()` function

```python
def main():
    import argparse, parsl

    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir",     default="/app/data")
    parser.add_argument("--work-dir",     default="/app/work/run0")
    parser.add_argument("--input-script", default="/app/data/in.watbox")
    args = parser.parse_args()

    frames_dir = run_lammps(args.input_script, args.data_dir, args.work_dir).result()
    output_csv = analyze_with_ovito(
        frames_dir, os.path.join(args.work_dir, "results.csv")
    ).result()

    print(f"Results written to: {output_csv}")
    parsl.clear()

if __name__ == "__main__":
    main()
```

---

### FILE 2: `run_workflow.sh`

```bash
#!/usr/bin/env bash
IMAGE="${1:-maw-sandbox:latest}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # /app/builds
REPO_DIR="$(dirname "$SCRIPT_DIR")"                           # /app (repo root)

echo "=== Starting workflow inside Docker: $IMAGE ==="
docker run --rm \
  -v "$REPO_DIR":/app \
  -w /app/builds \
  "$IMAGE" \
  python3 /app/builds/workflow.py \
  --data-dir /app/data \
  --work-dir /app/work/run0 \
  --input-script /app/data/in.watbox
```

**Critical rules:**
- NEVER use `$(pwd)` — always resolve via `${BASH_SOURCE[0]}`.
- Mount the **repo root** (one level above `builds/`) into the container at `/app`.
- Pass `--data-dir`, `--work-dir`, `--input-script` as arguments to `workflow.py`.
- Accept image tag as `$1`, default to `"maw-sandbox:latest"`.

---

## 6. Path Conventions (Docker-MAW)

| Location | Host path | Inside any container |
|---|---|---|
| Repo root | `HOST_REPO_PATH` env var | `/app` |
| LAMMPS input | `data/in.watbox` | `/app/data/in.watbox` |
| LAMMPS data | `data/data.init`, `data/AW.tersoff` | `/app/data/` |
| Generated files | `builds/` | `/app/builds/` |
| Work output | `work/run0/` | `/app/work/run0/` |

`HOST_REPO_PATH` is set via env var (defaults to `agent.py` directory). It is used in the executor's
`docker run -v "$HOST_REPO_PATH:/app"` command so the Docker daemon gets the real host path.

---

## 7. How the codegen Node Works

```python
def codegen(state: AgentState) -> dict:
    image_tag = state.get("image_tag", "maw-sandbox:latest")

    context = (
        f"Project layout:\n{PROJECT_LAYOUT}" +
        "\n\nLiterature findings:\n" + "\n".join(f"  - {f}" for f in state["literature_findings"]) +
        "\n\nTasks to implement:\n" + "\n".join(f"  {i+1}. {t}" for i, t in enumerate(state["tasks"])) +
        f"\n\nDocker image tag: {image_tag}" +
        feedback_section
    )

    result: CoderOutput = _invoke_structured(coder_llm, CoderOutput, [
        SystemMessage(content=CODEGEN_PROMPT),
        HumanMessage(content=context),
    ])

    # files written to builds/
    for code_file in result.files:
        with open(os.path.join(build_dir, code_file.filename), "w") as f:
            f.write(code_file.content)

    return {"code_output": [summary], "current_step": "codegen_complete"}
```

**Key points:**
- Uses `coder_llm` (defaults to `claudesonnet46`, overridable via `CODER_MODEL_NAME`).
- Context includes `PROJECT_LAYOUT` — a hardcoded string describing the full repo tree and absolute container paths.
- Uses `_invoke_structured()` — not `.with_structured_output()`.
- Files land in `builds/`.
- Returns only `code_output` and `current_step` — routing is always the orchestrator's job.

---

## 8. Dockerfile Rules (Installer — Jacob's domain, but codegen must be consistent)

The sandbox `Dockerfile` generated by the installer (in `builds/Dockerfile`) must:

1. `FROM ubuntu:22.04`
2. `ENV DEBIAN_FRONTEND=noninteractive`
3. Install system deps including `libopenmpi-dev openmpi-bin`
4. **MPI symlink (mandatory):** `RUN ln -sf $(find /usr/lib -name "libmpi.so.*" | grep -v libmpi_cxx | sort | tail -1) /usr/lib/x86_64-linux-gnu/libmpi.so.12 && ldconfig`
5. `pip3 install lammps` (wheel, not source)
6. `WORKDIR /app`
7. Headless rendering env vars: `LIBGL_ALWAYS_SOFTWARE=1`, `PYOPENGL_PLATFORM=osmesa`, `OVITO_GUI_MODE=0`

The MPI symlink is critical — the lammps pip wheel was compiled against `libmpi.so.12` but Ubuntu 22.04
ships a newer filename. Without it, LAMMPS crashes at import time.

---

## 9. Orchestrator Feedback Handling

When the orchestrator routes back to codegen with feedback, the node appends:

```
Orchestrator feedback — fix these issues in your new code:
<feedback text>
```

`CODEGEN_PROMPT` ends with:

```
HANDLING ORCHESTRATOR FEEDBACK:
If the input ends with a section titled "Orchestrator feedback", you MUST:
1. Read every point carefully
2. Fix every issue raised
3. Do not remove working logic — only fix what was flagged
4. YOU MUST FIX THE ISSUE THE ORCHESTRATOR IDENTIFIES.
```

Always preserve this section when modifying `CODEGEN_PROMPT`.

---

## 10. Common Pitfalls

| Pitfall | Rule |
|---|---|
| Imports at top of `@python_app` | All imports must be **inside** the function body |
| Calling lammps as subprocess | Use `from lammps import lammps; lmp = lammps(...)` |
| Hardcoded absolute paths | Use `os.path.join` + argparse; inside container use `/app/...` |
| `.result()` inside an app | Only call `.result()` in `main()` |
| `Pipeline()` constructor directly | Use `ovito.io.import_file(...)` |
| Extra Parsl kwargs (`max_workers` etc.) | Copy the config block exactly — extra kwargs cause `TypeError` |
| Missing `strategy="none"` in Parsl config | Include it — prevents auto-scaling issues in local mode |
| `$(pwd)` in run_workflow.sh | Use `${BASH_SOURCE[0]}` resolution instead |
| Mounting `builds/` instead of repo root | Always mount repo root at `/app`; `builds/` is a subdirectory |
| LLM returning markdown fences | Prompt says "ONLY valid JSON" — `_invoke_structured()` handles preamble but not fences inside JSON |
| `.with_structured_output()` | Not used here — use `_invoke_structured()` instead |
| `def_file_content` key in InstallerOutput | Key is `dockerfile_content` in Docker-MAW |
| Editing Ivy's section without warning | Always issue ownership warning first |
| Editing shared AgentState without warning | Always issue shared state warning first |

---

## 11. LLM & Model Config

```python
model     = ChatOpenAI(model=os.getenv("MODEL_NAME",       "claudeopus46"))
coder_llm = ChatOpenAI(model=os.getenv("CODER_MODEL_NAME", os.getenv("MODEL_NAME", "claudesonnet46")))
```

- **`model`** — orchestrator, planner (defaults to `claudeopus46`)
- **`coder_llm`** — installer, codegen (defaults to `claudesonnet46`)
- Override via `.env`: `MODEL_NAME`, `CODER_MODEL_NAME`, `OPENAI_BASE_URL`, `OPENAI_API_KEY`
- `HOST_REPO_PATH` — overrides the Docker bind mount source path (required when running agent inside Docker)

---

## 12. File Locations

| File | Purpose |
|---|---|
| `agent.py` | Full agent: all prompts, nodes, graph, entry point |
| `Dockerfile` | Agent container (python:3.11-slim + docker CLI) |
| `builds/Dockerfile` | Sandbox image (generated by installer) |
| `builds/workflow.py` | Generated by codegen |
| `builds/run_workflow.sh` | Generated by codegen |
| `data/in.watbox` | LAMMPS input script |
| `data/data.init` | LAMMPS initial atom data |
| `data/AW.tersoff` | LAMMPS force field |
| `Literature/` | Drop PDFs here |
| `skills/Workflow_CrashCourse.md` | Domain knowledge reference (existing) |
| `.opencode/skills/` | OpenCode skill files (this file lives here) |
| `PROJECT_CONTEXT.md` | Ownership rules, architecture, running instructions |
| `.env` | `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `MODEL_NAME`, `HOST_REPO_PATH` |

---

## 13. Generalizing to a New Scientific Domain

1. Keep the Parsl local executor config unchanged — it is domain-agnostic.
2. Replace `run_lammps` with a `@python_app` wrapping the new simulation tool.
3. Replace `analyze_with_ovito` with a `@python_app` wrapping the new analysis tool.
4. Keep `main()` structure: argparse → run simulation → run analysis → print summary → `parsl.clear()`.
5. Keep `run_workflow.sh` structure: `BASH_SOURCE` resolution, mount repo root at `/app`, call `python3 /app/builds/workflow.py`.
6. Update `PROJECT_LAYOUT` string in `agent.py` to reflect new file paths.
7. Always return ONLY valid JSON — `_invoke_structured()` tolerates preamble but not malformed JSON.

### New workflow checklist

- [ ] All imports inside `@python_app` / `@bash_app` bodies
- [ ] No subprocess calls for tools that have Python APIs
- [ ] `os.chdir(work_dir)` before running simulation (for relative dump paths)
- [ ] All paths via `os.path.join` or `/app/...` absolute container paths
- [ ] `.result()` only in `main()`
- [ ] `parsl.clear()` at end of `main()`
- [ ] `run_workflow.sh` uses `BASH_SOURCE` resolution, not `$(pwd)`
- [ ] `run_workflow.sh` mounts repo root (not `builds/`) at `/app`
- [ ] Parsl config copied exactly — no extra kwargs
- [ ] `strategy="none"` in Parsl Config
- [ ] LLM prompt ends with orchestrator feedback handling section
- [ ] Ownership warning issued before touching any `agent.py` section

import os
import json
import operator
import subprocess
import warnings
from datetime import datetime
from typing import Annotated, Literal, Sequence
from typing_extensions import TypedDict
from pydantic import BaseModel
from pypdf import PdfReader
warnings.filterwarnings("ignore", module="pypdf")
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages

load_dotenv()

console = Console()

_run_log_path: str = ""


# __ LLM _______________________________________________________________________
model     = ChatOpenAI(model=os.getenv("MODEL_NAME",       "claudeopus46"))
coder_llm = ChatOpenAI(model=os.getenv("CODER_MODEL_NAME", os.getenv("MODEL_NAME", "claudesonnet46")))


# __ Agent State _______________________________________________________________

class AgentState(TypedDict):
    messages:              Annotated[Sequence[BaseMessage], add_messages]
    goal:                  str
    pdf_path:              str
    literature_findings:   list[str]
    stack_decision:        list[str]
    tasks:                 list[str]
    code_output:           Annotated[list[str], operator.add]
    execution_output:      Annotated[list[str], operator.add]
    dockerfile:            str
    dockerfile_approved:   bool
    image_tag:             str
    current_step:          str
    orchestrator_feedback: str
    next:                  str
    planner_revisions:     int
    installer_revisions:   int
    codegen_revisions:     int
    executor_revisions:    int
    skill_update_summary:  str    # summary written by skill_updater after the run

# __ Pydantic Schemas __________________________________________________________

class OrchestratorOutput(BaseModel):
    reasoning:           str
    next:                Literal["planner", "installer", "codegen", "executor", "end"]
    feedback:            str
    dockerfile_approved: bool = False

class PlannerOutput(BaseModel):
    literature_findings: list[str]
    stack_decision:      list[str]
    tasks:               list[str]

class CodeFile(BaseModel):
    filename: str
    content:  str

class CoderOutput(BaseModel):
    files:           list[CodeFile]
    needs_more_info: bool = False
    missing_info:    str  = ""

class InstallerOutput(BaseModel):
    dockerfile_content: str

class SkillFileUpdate(BaseModel):
    skill_name:      str   # e.g. "codegen", "parsl", "auto_update"
    updated_content: str   # full new content of the SKILL.md file
    reason:          str   # one-sentence explanation of what changed and why

class SkillUpdaterOutput(BaseModel):
    updates: list[SkillFileUpdate]
    summary: str

# __ Agent Prompts _____________________________________________________________

ORCHESTRATOR_SYSTEM_PROMPT = """\
You are the supervisor orchestrator for a scientific workflow reproduction system.
You coordinate specialized agents to reproduce a computational workflow from a research paper inside a Docker container.

After each agent completes, you review its output critically and decide where to route next.

Agents:
- planner    — reads the PDF, extracts literature findings, dependency stack, and ordered tasks
- installer  — generates a Dockerfile and builds a Docker image with all required packages installed
- codegen    — generates Parsl workflow code AND a Docker run launcher script for LAMMPS + OVITO (merged into one agent)
- executor   — runs the generated workflow inside the Docker container, captures stdout/stderr
- end        — signals successful completion

General flow (follow unless you have reason to deviate):
planner → installer → codegen → executor → end

Runtime environment:
The workflow runs inside a single local Docker container on a developer machine — NOT an HPC cluster.
There is no MPI network fabric, no SLURM, no multi-node communication, and no shared filesystem across nodes.
When reviewing agent outputs or giving feedback, always reason in terms of single-node, single-process execution.
LAMMPS runs via the Python API (from lammps import lammps) in serial mode — do NOT recommend mpirun, openmpi, mpich, or any MPI packages to the installer. MPI is not needed and will cause crashes in this environment.

You MUST review each agent's output before proceeding. Route BACK with specific feedback if:
- After planner:   tasks are vague, parameters are missing, or stack is wrong for the paper
- After installer: Dockerfile is missing packages, uses wrong base image, or doesn't match stack_decision
- After codegen:   code is structurally incomplete (missing functions, missing main(), no files generated). Do NOT invent runtime errors — you cannot execute code. If the code looks complete and plausible, route to executor immediately.
- After executor: execution failed
    → If stderr contains "ModuleNotFoundError: No module named" or "ImportError: No module named",
      route to installer with the exact missing package name in the feedback field.
    → For ALL other failures (LAMMPS errors, Parsl errors, logic errors, segfaults, wrong output,
      file not found), route to codegen with the full stderr. LAMMPS runtime errors are code errors,
      not missing packages.

When routing back, always provide specific, actionable feedback in the feedback field.
When proceeding forward normally, set feedback to empty string.
When execution succeeds, route to "end".

TWO-PHASE INSTALLER REVIEW:
The installer works in two phases and requires your explicit sign-off between them:
  Phase 1 — generates the Dockerfile and stops. current_step will be "installer_dockerfile_pending_approval".
  Phase 2 — builds the actual Docker image. Only runs after you approve.

When current_step is "installer_dockerfile_pending_approval", you MUST review dockerfile and either:
  APPROVE: set dockerfile_approved=true, next="installer", feedback=""
    → installer will proceed to build the Docker image
  REJECT:  set dockerfile_approved=false, next="installer", feedback="<specific issues>"
    → installer will regenerate the Dockerfile and return for review again

What to check in the Dockerfile:
- All packages from stack_decision are present
- Base image is ubuntu:24.04 (NOT 22.04 — 24.04 is required for OVITO ARM64 support)
- LAMMPS installed via pip (not source build)
- libopenmpi3 is present in the apt-get install list — this is REQUIRED and CORRECT.
  The pip lammps wheel links against libmpi.so.12 at runtime. libopenmpi3 provides
  ONLY the shared library file, not mpirun or mpiexec. LAMMPS still runs in serial mode.
  Do NOT reject a Dockerfile for including libopenmpi3.
- pip install commands use --break-system-packages flag (required on Ubuntu 24.04)
- ENV, RUN, and WORKDIR instructions are all present and correct
- No conda, no mamba
- No openmpi-bin, no mpirun, no mpiexec, no mpi4py (these are NOT needed and NOT allowed)

In all situations other than approving a pending Dockerfile, set dockerfile_approved=false.

State fields available to you:
- goal, pdf_path, current_step
- literature_findings, stack_decision, tasks   (from planner)
- dockerfile                                   (from installer phase 1 — review before approving)
- code_output                                  (from codegen)
- execution_output                             (from executor — stdout/stderr)


Return ONLY a valid JSON object with exactly these keys:
- reasoning:          your analysis of the current state and why you are routing where you are
- next:               one of "planner", "installer", "codegen", "executor", "end"
- feedback:           specific actionable criticism for the receiving agent, or empty string if proceeding normally
- dockerfile_approved: true only when approving a pending Dockerfile, false in all other cases
\
"""

PLANNER_PROMPT = """\
You are a scientific workflow analyst. You will be given the full text of a research paper and a goal.

Runtime environment: The workflow runs inside a single local Docker container on a developer machine — NOT an HPC cluster. There is no SLURM, no MPI across nodes, no job scheduler, and no HPC infrastructure. Recommend only tools and packages that run in a single process inside a Docker container. Do NOT recommend MPI, mpirun, OpenMPI, MPICH, mpi4py, SLURM, PBS, or any HPC-specific parallelism tools.

Extract everything needed to reproduce the computational workflow described in the paper and return it as a JSON object with exactly these three keys:
- literature_findings: list of strings — key methods, parameters, and scientific context needed to reproduce the workflow
- stack_decision: list of strings — all required software and Python packages with versions where known
- tasks: list of strings — ordered concrete implementation steps, each describing what to do, what tool to use, and what parameters are involved

Be specific and technical. The target workflow uses LAMMPS for molecular dynamics simulation of water freezing, OVITO for diamond structure detection, and Parsl for workflow orchestration.

Return ONLY a valid JSON object. No markdown, no code fences, no explanation.

---

EXAMPLE OUTPUT:
{
  "literature_findings": [
    "The workflow uses LAMMPS molecular dynamics to simulate water crystallization using the TIP4P/Ice force field at 210K, 220K, and 230K undercoolings",
    "Diamond structure identification is performed in-situ using OVITO's IdentifyDiamondModifier",
    "Parsl @python_app decorators are used to define LAMMPS simulation and OVITO analysis as parallel tasks"
  ],
  "stack_decision": ["lammps", "ovito>=3.0", "parsl>=2024.0.0", "numpy", "python>=3.10"],
  "tasks": [
    "Write a LAMMPS input script that initializes a 4000-atom TIP4P/Ice water box at 210K",
    "Define a Parsl @python_app function run_lammps(input_script, workdir) that calls lmp.file(input_script)",
    "Define a Parsl @python_app function analyze_with_ovito(frames_glob, out_png) using IdentifyDiamondModifier"
  ]
}

---

HANDLING ORCHESTRATOR FEEDBACK:
If the input ends with a section titled "Orchestrator feedback", it means a previous version of your output was rejected. You MUST:
1. Read every point in the feedback carefully
2. Identify exactly what was wrong or missing
3. Fix each issue in your new output — do not repeat the same mistakes
4. Your revised output will be reviewed again before the workflow proceeds

Now apply this same structure and level of detail to the paper and goal provided.\
"""

INSTALLER_PROMPT = """\
You are a Docker environment specialist. Given a list of required software packages and scientific workflow context, generate a valid Dockerfile that installs all dependencies into an Ubuntu 22.04 container.

Runtime environment: This container runs on a single developer machine NOT an HPC cluster. There is no MPI network fabric, no SLURM, and no multi-node communication. Prefer single-node, serial-friendly package configurations. Avoid HPC-specific MPI setups unless strictly required by the workflow.

Return a JSON object with exactly one key:
- dockerfile_content: string — the complete, valid Dockerfile

The Dockerfile MUST:
1. Start with: FROM ubuntu:24.04
   REASON: Ubuntu 24.04 ships glibc 2.39 and Python 3.12, both required by the OVITO ARM64 wheel.
   Do NOT use ubuntu:22.04 — it has glibc 2.35 and Python 3.10, which are incompatible with OVITO on ARM64.
2. Set: ENV DEBIAN_FRONTEND=noninteractive
3. Install system dependencies INCLUDING the OpenMPI runtime shared library:
   RUN apt-get update && apt-get install -y python3 python3-pip python3-dev build-essential wget git libosmesa6 libgl1 libglib2.0-0 libopenmpi3 && rm -rf /var/lib/apt/lists/*
   NOTE: Ubuntu 24.04 renamed libgl1-mesa-glx to libgl1 and libosmesa6-dev to libosmesa6. Always use libgl1 and libosmesa6 — NOT libgl1-mesa-glx or libosmesa6-dev.
   IMPORTANT: libopenmpi3 provides ONLY the shared library (.so file) that the pip lammps wheel links against at runtime.
   It does NOT install mpirun or mpiexec. LAMMPS still runs in serial mode via the Python API — not via mpirun.
   Without libopenmpi3, the pip lammps wheel will crash with "libmpi.so.12: cannot open shared object file".
4. Upgrade pip: RUN pip3 install --upgrade pip --break-system-packages
5. Install LAMMPS via pip wheel: RUN pip3 install lammps --break-system-packages
6. Install OVITO: RUN pip3 install ovito --break-system-packages
7. Install all other pip-installable packages from the provided stack list, each with --break-system-packages
8. Set working directory: WORKDIR /app
9. Set headless rendering env vars:
   ENV LIBGL_ALWAYS_SOFTWARE=1
   ENV PYOPENGL_PLATFORM=osmesa
   ENV OVITO_GUI_MODE=0
   ENV LD_LIBRARY_PATH=/usr/lib/aarch64-linux-gnu:/usr/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH

Rules:
- ALWAYS install libopenmpi3 in the apt-get step — the pip lammps wheel requires it as a runtime dependency
- Do NOT install openmpi-bin, mpirun, mpiexec, libopenmpi-dev, libmpich-dev, or mpi4py
- Do NOT build LAMMPS from source
- Do NOT use conda or mamba
- Always use --break-system-packages on every pip3 install — Ubuntu 24.04 requires it
- Do NOT include markdown code fences or any text outside the JSON
- Return ONLY a valid JSON object

EXAMPLE OUTPUT:
{
  "dockerfile_content": "<complete valid Dockerfile as a single string with \\n line breaks>"
}

---

HANDLING ORCHESTRATOR FEEDBACK:
If the input ends with a section titled "Orchestrator feedback", it means a previous version of your Dockerfile was rejected. You MUST:
1. Read every point in the feedback carefully — it may flag missing packages, wrong base image, incorrect syntax, or packages that caused runtime errors
2. Fix every issue raised — add missing packages, correct apt-get dependencies, adjust versions
3. Do not remove packages that were already correct — only fix what was flagged
4. Your revised Dockerfile will be reviewed again before the build runs

Now apply this same structure to the stack and findings provided.\
"""

CODEGEN_PROMPT = """\
You are a scientific workflow code generator. Your job is to write Python code that uses Parsl to orchestrate a LAMMPS molecular dynamics simulation followed by OVITO structural analysis, running inside a local Docker container on a developer machine — NOT an HPC cluster. There is no SLURM, no MPI across nodes, and no HPC job scheduler. Use single-node, single-process configurations only.

You will be given:
- A list of literature findings (methods, parameters, scientific context)
- An ordered list of tasks to implement
- The Docker image tag for the sandbox container
- The path to the LAMMPS input files (in.watbox, data.init, AW.tersoff)

Generate a complete, working Python workflow. Return a JSON object with exactly one key:
- files: list of objects, each with:
    - filename: string (e.g. "workflow.py")
    - content: string (the full file content)

You MUST generate exactly these two files:

---

FILE 1: workflow.py
A self-contained Parsl workflow script. It must:

1. Import Parsl and configure it using a LOCAL executor by default (so it works on any machine without a scheduler):
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
   CRITICAL: Do NOT add max_workers, max_workers_per_node, or any other kwargs not shown above — they do not exist in recent Parsl versions and will cause a TypeError at startup. Copy this config exactly.

2. Define a Parsl @python_app called run_lammps(input_script, data_dir, work_dir) that:
   - Creates work_dir if it does not exist
   - Copies data.init and AW.tersoff from data_dir into work_dir
   - Also copies the input script into work_dir
   - Creates a "frames/" subdirectory inside work_dir (LAMMPS dumps trajectories there)
   - CRITICAL: calls os.chdir(work_dir) BEFORE running LAMMPS — the input script dumps to "frames/" relative to CWD, so the CWD must be work_dir or frames will go to the wrong place:
     ```python
     import os
     os.chdir(work_dir)
     os.makedirs("frames", exist_ok=True)
     from lammps import lammps
     lmp = lammps(cmdargs=["-screen", "none"])
     lmp.file(os.path.join(work_dir, os.path.basename(input_script)))
     lmp.close()
     ```
   - Returns the path to the frames directory (os.path.join(work_dir, "frames"))

3. Define a Parsl @python_app called analyze_with_ovito(frames_dir, output_csv) that:
   - Uses ovito.io.import_file with a glob pattern to load all LAMMPS dump files from frames_dir
   - Applies ovito.modifiers.IdentifyDiamondModifier to identify ice-like (diamond cubic) atoms
   - Iterates over all frames using pipeline.compute(frame_index)
   - Extracts the count of cubic_diamond and hexagonal_diamond type atoms per frame
   - Writes a CSV file to output_csv with columns: frame, timestep, cubic_diamond_count, hexagonal_diamond_count
   - Returns the path to the output CSV

4. A main() function that:
   - Accepts DATA_DIR and WORK_DIR as constants or argparse arguments
   - Calls run_lammps(...).result() to block until LAMMPS is done
   - Calls analyze_with_ovito(...).result() to block until OVITO is done
   - Prints a summary of results from the CSV
   - Calls parsl.clear() at the end

5. if __name__ == "__main__": calls main()

---

FILE 2: run_workflow.sh
A bash launcher script that runs workflow.py inside the Docker container. It must:
- Accept the image tag as $1 (first argument), defaulting to "maw-sandbox:latest" if not provided
- Resolve the repo root using SCRIPT_DIR/REPO_DIR — NEVER use $(pwd):
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    REPO_DIR="$(dirname "$SCRIPT_DIR")"
- Use: docker run --rm -v "$REPO_DIR":/app -w /app/builds "$IMAGE" python3 /app/builds/workflow.py
- Pass --data-dir /app/data --work-dir /app/work/run0 --input-script /app/data/in.watbox as arguments to workflow.py
- Print a clear start message before launching

---

IMPORTANT RULES:
- Use only the lammps Python wheel API (from lammps import lammps) — do NOT call the lammps binary as a subprocess
- Use ovito.io.import_file() to create the pipeline — do NOT call Pipeline() constructor directly. Import modifiers from ovito.modifiers.
- All file paths must be constructed with os.path.join — no hardcoded absolute paths
- The LAMMPS dump command in in.watbox writes to "frames/step.*.lammpstrj" relative to CWD — your code must cd into work_dir or set the working directory so these paths resolve correctly
- Do NOT use conda; all packages are already pip-installed in the container
- Return ONLY a valid JSON object with the "files" key — no markdown, no code fences, no explanation outside the JSON

---

HANDLING ORCHESTRATOR FEEDBACK:
If the input ends with a section titled "Orchestrator feedback", it means a previous version of your code was rejected. You MUST:
1. Read every point in the feedback carefully — it may flag import errors, wrong API usage, path issues, or runtime failures
2. Fix every issue raised in the new version of the code
3. Do not remove working logic — only fix what was flagged
4. Your revised code will be reviewed and re-executed
5. YOU MUST FIX THE ISSUE THE ORCHESTRATOR IDENTIFIES. 

Now generate the workflow code based on the literature findings and tasks provided.\
"""

EXECUTOR_PROMPT = "TODO"

SKILL_UPDATER_PROMPT = """\
You are a meta-learning agent responsible for improving AI skill files based on what just happened in a workflow run.

You will be given:
- A summary of the run: revision counts, exit code, orchestrator feedback, errors from execution output
- The current content of each skill file that may need updating

Your job is to return an updated version of any skill files that need improvement.

RULES:
1. Preserve all existing section numbers and headers - do not reorder or rename sections.
2. Only add or modify content within sections - specifically the pitfalls table, code examples, and checklists.
3. Be specific - new pitfall rows must include the exact error message pattern or a recognizable excerpt.
4. No duplication - if a pitfall already exists, update it rather than adding a duplicate row.
5. Only add things that actually happened in this run - do not invent pitfalls not evidenced in the state.
6. Every addition must be a concrete rule or code example, not a vague observation.
7. If no update is needed for a skill file, do not include it in the updates list.
8. updated_content must be the COMPLETE new file content - not a diff, not a partial excerpt.
9. Never modify Section 0 (ownership rules) of codegen/SKILL.md.

Return ONLY a valid JSON object with exactly these keys:
- updates: list of objects, each with skill_name (string), updated_content (string), reason (string)
- summary: one short paragraph summarizing what was learned from this run and what changed\
"""

# __ Project layout (injected into codegen context) ____________________________

PROJECT_LAYOUT = """\
Repo directory tree — the repo root is always mounted at /app inside every container:

/app/                                  ← repo root
├── agent.py
├── Dockerfile                         ← agent container image definition
├── requirements.txt
├── .env
├── data/
│   ├── in.watbox                      ← LAMMPS input script
│   ├── data.init                      ← LAMMPS initial atom positions
│   └── AW.tersoff                     ← LAMMPS force field parameters
├── Literature/
│   └── *.pdf
└── builds/                            ← ALL generated files land here
    ├── Dockerfile                     ← sandbox image definition (generated by installer)
    ├── workflow.py                    ← Parsl workflow script (generated by codegen)
    └── run_workflow.sh                ← launcher script (generated by codegen)

Absolute paths inside any container (agent or sandbox):
  LAMMPS input script : /app/data/in.watbox
  LAMMPS data files   : /app/data/data.init  and  /app/data/AW.tersoff
  workflow.py         : /app/builds/workflow.py
  run_workflow.sh     : /app/builds/run_workflow.sh
  work output dir     : /app/work/run0  (created at runtime)

run_workflow.sh is at /app/builds/run_workflow.sh.
It must mount the REPO ROOT (one level above builds/) into the sandbox at /app.
Resolve paths like this — never use $(pwd):
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # /app/builds
  REPO_DIR="$(dirname "$SCRIPT_DIR")"                           # /app
  docker run --rm -v "$REPO_DIR":/app -w /app/builds "$IMAGE" python3 /app/builds/workflow.py ...\
"""

# Host-side repo path for Docker bind mounts.
# Inside the agent container __file__ resolves to /app; the HOST Docker daemon
# needs the real host path so volume mounts on the sandbox container work correctly.
HOST_REPO_PATH = os.environ.get("HOST_REPO_PATH", os.path.dirname(os.path.abspath(__file__)))

# __ Structured output helper __________________________________________________

def _invoke_structured(llm, schema, messages, retries=5):
    """Call llm and parse the response as schema, tolerating preamble text before the JSON block."""
    import json as _json, re as _re
    last_err = None
    for attempt in range(retries):
        response = llm.invoke(messages)
        text = response.content if hasattr(response, "content") else str(response)
        match = _re.search(r'\{.*\}', text, _re.DOTALL)
        if not match:
            last_err = ValueError(f"No JSON object found in model response:\n{text[:500]}")
            console.print(f"[yellow][_invoke_structured] attempt {attempt+1}: no JSON found, retrying...[/yellow]")
            continue
        try:
            return schema.model_validate(_json.loads(match.group(0)))
        except (_json.JSONDecodeError, Exception) as e:
            last_err = e
            console.print(f"[yellow][_invoke_structured] attempt {attempt+1}: parse error ({e}), retrying...[/yellow]")
    raise last_err

# __ Nodes _____________________________________________________________________

def orchestrator(state: AgentState) -> dict:
    console.print("\n[dim cyan][orchestrator] reviewing state...[/dim cyan]")

    revisions = {
        "planner":   state.get("planner_revisions",   0),
        "installer": state.get("installer_revisions", 0),
        "codegen":   state.get("codegen_revisions",   0),
        "executor":  state.get("executor_revisions",  0),
    }
    if any(revisions.values()):
        rev_str = " | ".join(f"{k}: {v}" for k, v in revisions.items() if v > 0)
        console.print(f"[dim yellow][orchestrator] revision counts — {rev_str}[/dim yellow]")

    parts = [f"Goal: {state['goal']}", f"Current step: {state['current_step']}"]
    if state.get("literature_findings"):
        parts.append(f"Literature findings ({len(state['literature_findings'])} items):\n" +
                     "\n".join(f"  - {f}" for f in state["literature_findings"]))
    if state.get("stack_decision"):
        parts.append(f"Stack: {state['stack_decision']}")
    if state.get("tasks"):
        parts.append(f"Tasks ({len(state['tasks'])}):\n" +
                     "\n".join(f"  {i+1}. {t}" for i, t in enumerate(state["tasks"])))
    if state.get("dockerfile"):
        parts.append(f"Dockerfile:\n{state['dockerfile']}")
    if state.get("code_output"):
        parts.append(f"Code output (latest):\n{state['code_output'][-1]}")
    if state.get("execution_output") and state.get("current_step") != "installer_dockerfile_pending_approval":
        parts.append(f"Execution output (latest):\n{state['execution_output'][-1]}")

    result: OrchestratorOutput = _invoke_structured(model, OrchestratorOutput, [
        SystemMessage(content=ORCHESTRATOR_SYSTEM_PROMPT),
        HumanMessage(content="\n\n".join(parts)),
    ])

    # Hard overrides: lock routing at deterministic transition points
    if state.get("current_step") == "installer_dockerfile_pending_approval":
        result.next = "installer"
    elif state.get("current_step") == "installer_complete":
        result.next = "codegen"

    panel_body = f"[bold]Routing to:[/bold] [green]{result.next}[/green]\n\n[bold]Reasoning:[/bold]\n{result.reasoning}"
    if result.feedback:
        panel_body += f"\n\n[bold]Feedback to {result.next}:[/bold]\n[yellow]{result.feedback}[/yellow]"
    console.print(Panel(panel_body, title="[bold cyan]Orchestrator Decision[/bold cyan]", border_style="cyan"))

    if _run_log_path:
        with open(_run_log_path, "a") as _lf:
            _lf.write(json.dumps({
                "ts":         datetime.now().isoformat(),
                "from_step":  state.get("current_step"),
                "routing_to": result.next,
                "revisions":  revisions,
                "feedback":   result.feedback,
                "reasoning":  result.reasoning[:500],
            }) + "\n")

    already_ran = {
        "planner":   bool(state.get("literature_findings")),
        "installer": bool(state.get("dockerfile")),
        "codegen":   bool(state.get("code_output")),
        "executor":  bool(state.get("execution_output")),
    }
    revision_update = {}
    if result.next in already_ran and already_ran[result.next]:
        key = f"{result.next}_revisions"
        revision_update[key] = state.get(key, 0) + 1
        console.print(f"[bold yellow][orchestrator] revision #{revision_update[key]} for {result.next}[/bold yellow]")

    return {
        "next":                  result.next,
        "orchestrator_feedback": result.feedback,
        "dockerfile_approved":   result.dockerfile_approved,
        "current_step":          f"orchestrator_routed_to_{result.next}",
        **revision_update,
    }


def planner(state: AgentState) -> dict:
    try:
        console.print("\n[dim cyan][planner] reading PDF...[/dim cyan]")

        reader   = PdfReader(state["pdf_path"])
        pdf_text = "\n".join(page.extract_text() for page in reader.pages if page.extract_text())
        console.print(f"[dim cyan][planner] loaded {len(reader.pages)} pages[/dim cyan]")

        feedback = state.get("orchestrator_feedback", "")
        feedback_section = (f"\n\nOrchestrator feedback — address these issues before returning:\n{feedback}"
                            if feedback else "")

        result: PlannerOutput = _invoke_structured(model, PlannerOutput, [
            SystemMessage(content=PLANNER_PROMPT),
            HumanMessage(content=f"Goal: {state['goal']}\n\nPaper:\n{pdf_text}{feedback_section}"),
        ])

        console.print(f"[dim cyan][planner] produced {len(result.tasks)} tasks[/dim cyan]")

        findings = "\n".join(f"  [cyan]•[/cyan] {f}" for f in result.literature_findings)
        stack    = "\n".join(f"  [cyan]•[/cyan] {s}" for s in result.stack_decision)
        tasks    = "\n".join(f"  [bold]{i+1}.[/bold] {t}" for i, t in enumerate(result.tasks))
        console.print(Panel(
            f"[bold]Literature Findings[/bold]\n{findings}\n\n"
            f"[bold]Stack[/bold]\n{stack}\n\n"
            f"[bold]Tasks[/bold]\n{tasks}",
            title="[bold green]Planner Output[/bold green]",
            border_style="green",
        ))

        return {
            "literature_findings": result.literature_findings,
            "stack_decision":      result.stack_decision,
            "tasks":               result.tasks,
            "current_step":        "planner_complete",
        }
    except Exception as e:
        console.print(f"[red][planner] ERROR: {e}[/red]")
        raise


def installer(state: AgentState) -> dict:
    try:
        build_dir       = os.path.join(os.path.dirname(os.path.abspath(__file__)), "builds")
        os.makedirs(build_dir, exist_ok=True)
        dockerfile_path = os.path.join(build_dir, "Dockerfile")
        image_tag       = "maw-sandbox:latest"

        if state.get("dockerfile_approved"):
            # ── Phase 2: Dockerfile approved — build the image ───────────────
            import shutil, hashlib
            docker_available = shutil.which("docker") is not None

            if not docker_available:
                console.print(
                    "[yellow][installer] docker not found. "
                    "Skipping image build — Dockerfile written. "
                    "Run 'docker build -t maw-sandbox:latest -f builds/Dockerfile .' to build.[/yellow]"
                )
                return {
                    "image_tag":    image_tag,
                    "current_step": "installer_complete",
                }

            # ── skip rebuild if Dockerfile unchanged and image already exists ─
            hash_file = os.path.join(build_dir, ".dockerfile_hash")
            with open(dockerfile_path) as f:
                current_content = f.read()
            current_hash = hashlib.md5(current_content.encode()).hexdigest()

            image_exists = subprocess.run(
                ["docker", "inspect", image_tag], capture_output=True,
            ).returncode == 0

            stored_hash = ""
            if os.path.isfile(hash_file):
                with open(hash_file) as f:
                    stored_hash = f.read().strip()

            if current_hash == stored_hash and image_exists:
                console.print("[dim cyan][installer] Dockerfile unchanged and image exists — skipping rebuild[/dim cyan]")
                return {
                    "image_tag":    image_tag,
                    "current_step": "installer_complete",
                }

            console.print("[dim cyan][installer] Dockerfile approved — building Docker image (this may take several minutes)...[/dim cyan]")

            build_context = os.path.dirname(os.path.abspath(__file__))
            proc = subprocess.run(
                ["docker", "build", "--network=host", "-t", image_tag, "-f", dockerfile_path, build_context],
                capture_output=True, text=True,
                timeout=1800,
            )

            if proc.returncode != 0:
                raise RuntimeError(f"docker build failed (exit {proc.returncode}):\n{proc.stderr[-3000:]}")

            with open(hash_file, "w") as f:
                f.write(current_hash)

            console.print(f"[dim cyan][installer] image built: {image_tag}[/dim cyan]")
            return {
                "image_tag":    image_tag,
                "current_step": "installer_complete",
            }

        else:
            # ── Phase 1: generate Dockerfile and return for orchestrator review ──
            console.print("\n[dim cyan][installer] generating Dockerfile for review...[/dim cyan]")

            feedback = state.get("orchestrator_feedback", "")
            feedback_section = (f"\n\nOrchestrator feedback — fix these issues:\n{feedback}"
                                if feedback else "")

            result: InstallerOutput = _invoke_structured(coder_llm, InstallerOutput, [
                SystemMessage(content=INSTALLER_PROMPT),
                HumanMessage(content=(
                    "Stack:\n" + "\n".join(state["stack_decision"]) +
                    "\n\nFindings:\n" + "\n".join(state["literature_findings"]) +
                    feedback_section
                )),
            ])

            # ── Post-process: enforce platform-safe base image and pip flags ────
            import re as _re
            dockerfile = result.dockerfile_content

            # 1. Force base image to ubuntu:24.04
            dockerfile = _re.sub(r'FROM\s+ubuntu:\S+', 'FROM ubuntu:24.04', dockerfile)

            # 2. Clean up any duplicate or malformed --break-system-package(s) flags
            #    The LLM sometimes generates "--break-system-package --break-system-packagess"
            #    Normalise every pip3 install line: remove all variants then add one clean copy.
            def fix_pip_line(line: str) -> str:
                if 'pip3' not in line and 'pip ' not in line:
                    return line
                # Remove the "upgrade pip" line entirely — on Ubuntu 24.04 it
                # fails because Debian's pip has no RECORD file and can't be
                # uninstalled. pip 24.0 ships with the image and is sufficient.
                if _re.search(r'pip3?\s+install\b.*--upgrade\s+pip\b', line):
                    return ''
                if _re.search(r'pip3?\s+install\b.*\bupgrade\b.*\bpip\b', line):
                    return ''
                # Strip all variations of the --break-system-packages flag (including typos)
                cleaned = _re.sub(r'\s+--break-system-package[s]*', '', line)
                # Re-add exactly once if this is a pip install line
                if _re.search(r'pip3?\s+install\b', cleaned):
                    cleaned = cleaned.rstrip() + ' --break-system-packages'
                return cleaned

            dockerfile = '\n'.join(
                l for l in (fix_pip_line(l) for l in dockerfile.splitlines())
                if l is not None
            )

            console.print("[dim cyan][installer] applied platform fixes: ubuntu:24.04, --break-system-packages normalised[/dim cyan]")

            with open(dockerfile_path, "w") as f:
                f.write(dockerfile)

            console.print(Panel(
                dockerfile,
                title="[bold yellow]Dockerfile — Pending Orchestrator Approval[/bold yellow]",
                border_style="yellow",
            ))
            console.print("[dim yellow][installer] Dockerfile written — waiting for orchestrator approval before building image...[/dim yellow]")

            return {
                "dockerfile":   dockerfile,
                "current_step": "installer_dockerfile_pending_approval",
            }

    except Exception as e:
        console.print(f"[red][installer] ERROR: {e}[/red]")
        raise


def codegen(state: AgentState) -> dict:
    try:
        console.print("\n[dim cyan][codegen] generating workflow code...[/dim cyan]")

        feedback = state.get("orchestrator_feedback", "")
        feedback_section = (f"\n\nOrchestrator feedback — fix these issues in your new code:\n{feedback}"
                            if feedback else "")

        image_tag = state.get("image_tag", "maw-sandbox:latest")

        context = (
            f"Project layout:\n{PROJECT_LAYOUT}" +
            "\n\nLiterature findings:\n" + "\n".join(f"  - {f}" for f in state.get("literature_findings", [])) +
            "\n\nTasks to implement:\n" + "\n".join(f"  {i+1}. {t}" for i, t in enumerate(state.get("tasks", []))) +
            f"\n\nDocker image tag: {image_tag}" +
            feedback_section
        )

        result: CoderOutput = _invoke_structured(coder_llm, CoderOutput, [
            SystemMessage(content=CODEGEN_PROMPT),
            HumanMessage(content=context),
        ])

        build_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "builds")
        os.makedirs(build_dir, exist_ok=True)

        written = []
        for code_file in result.files:
            file_path = os.path.join(build_dir, code_file.filename)
            with open(file_path, "w") as f:
                f.write(code_file.content)
            written.append(f"{code_file.filename} ({len(code_file.content)} chars)")
            console.print(Panel(
                code_file.content,
                title=f"[bold green]Generated: {code_file.filename}[/bold green]",
                border_style="green",
            ))

        console.print(f"[dim cyan][codegen] wrote {len(result.files)} file(s): {', '.join(written)}[/dim cyan]")

        if result.needs_more_info:
            console.print(f"[yellow][codegen] needs more info: {result.missing_info}[/yellow]")

        summary_parts = []
        for code_file in result.files:
            summary_parts.append(f"=== {code_file.filename} ===\n{code_file.content}")
        summary = "\n\n".join(summary_parts)

        return {
            "code_output":  [summary],
            "current_step": "codegen_complete",
        }
    except Exception as e:
        console.print(f"[red][codegen] ERROR:[/red] {e}")
        raise


def executor(state: AgentState) -> dict:
    try:
        console.print("\n[dim cyan][executor] running workflow in Docker container...[/dim cyan]")

        repo_dir    = os.path.dirname(os.path.abspath(__file__))
        build_dir   = os.path.join(repo_dir, "builds")
        workflow_py = os.path.join(build_dir, "workflow.py")
        image_tag   = state.get("image_tag", "maw-sandbox:latest")

        import shutil
        docker_available = shutil.which("docker") is not None

        if not docker_available:
            msg = (
                "docker not available on this machine. "
                "To execute the workflow, ensure Docker is installed and run:\n"
                f"  bash builds/run_workflow.sh {image_tag}"
            )
            console.print(f"[yellow][executor] {msg}[/yellow]")
            output = f"SKIPPED (no docker): {msg}"
            return {
                "execution_output": [output],
                "current_step":     "executor_complete",
            }

        if not os.path.isfile(workflow_py):
            raise FileNotFoundError(f"workflow.py not found at {workflow_py}. Run codegen first.")

        cmd = [
            "docker", "run", "--rm",
            "-v", f"{HOST_REPO_PATH}:/app",
            "-w", "/app/builds",
            "-e", "LIBGL_ALWAYS_SOFTWARE=1",
            "-e", "PYOPENGL_PLATFORM=osmesa",
            "-e", "OVITO_GUI_MODE=0",
            image_tag,
            "python3", "workflow.py",
            "--data-dir",     "/app/data",
            "--work-dir",     "/app/work/run0",
            "--input-script", "/app/data/in.watbox",
        ]

        console.print(f"[dim cyan][executor] command: {' '.join(cmd)}[/dim cyan]")
        console.print("[dim cyan][executor] this may take several minutes...[/dim cyan]")

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,
        )

        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        combined = (
            f"=== EXIT CODE: {proc.returncode} ===\n\n"
            f"=== STDOUT ===\n{stdout}\n\n"
            f"=== STDERR ===\n{stderr}"
        )

        color = "green" if proc.returncode == 0 else "red"
        console.print(Panel(
            combined[:6000],
            title=f"[bold {color}]Executor Output (exit {proc.returncode})[/bold {color}]",
            border_style=color,
        ))

        if proc.returncode != 0:
            console.print(f"[red][executor] workflow exited with code {proc.returncode}[/red]")
        else:
            console.print("[dim cyan][executor] workflow completed successfully[/dim cyan]")

        return {
            "execution_output": [combined],
            "current_step":     "executor_complete",
        }

    except Exception as e:
        console.print(f"[red][executor] ERROR:[/red] {e}")
        raise


def skill_updater(state: AgentState) -> dict:
    try:
        console.print("\n[dim cyan][skill_updater] analyzing run history...[/dim cyan]")

        repo_root   = os.path.dirname(os.path.abspath(__file__))
        skills_root = os.path.join(repo_root, ".opencode", "skills")

        def skill_path(name: str) -> str:
            return os.path.join(skills_root, name, "SKILL.md")

        def read_skill(name: str) -> str:
            path = skill_path(name)
            if os.path.isfile(path):
                with open(path) as f:
                    return f.read()
            return ""

        # ── build run summary ─────────────────────────────────────────────────
        revisions = {
            "planner":   state.get("planner_revisions",   0),
            "installer": state.get("installer_revisions", 0),
            "codegen":   state.get("codegen_revisions",   0),
            "executor":  state.get("executor_revisions",  0),
        }
        execution_tail = ""
        if state.get("execution_output"):
            execution_tail = state["execution_output"][-1][:3000]

        feedback = state.get("orchestrator_feedback", "")

        exit_code_line = ""
        for line in execution_tail.splitlines():
            if "EXIT CODE" in line:
                exit_code_line = line.strip()
                break

        run_summary = (
            f"Revision counts: {revisions}\n"
            f"Last orchestrator feedback: {feedback or '(none)'}\n"
            f"Docker image tag: {state.get('image_tag', 'maw-sandbox:latest')}\n"
            f"{exit_code_line}\n\n"
            f"Execution output (truncated):\n{execution_tail}"
        )

        # ── read current skill files ──────────────────────────────────────────
        skill_names = ["codegen", "parsl", "auto_update"]
        skill_contents = {name: read_skill(name) for name in skill_names}

        context = (
            f"Run summary:\n{run_summary}\n\n" +
            "\n\n".join(
                f"=== Current content of {name}/SKILL.md ===\n{content}"
                for name, content in skill_contents.items()
                if content
            )
        )

        # ── call LLM ─────────────────────────────────────────────────────────
        result: SkillUpdaterOutput = _invoke_structured(model, SkillUpdaterOutput, [
            SystemMessage(content=SKILL_UPDATER_PROMPT),
            HumanMessage(content=context),
        ])

        # ── write updated skill files ─────────────────────────────────────────
        for update in result.updates:
            path = skill_path(update.skill_name)
            if not os.path.isfile(path):
                console.print(f"[yellow][skill_updater] skipping unknown skill: {update.skill_name}[/yellow]")
                continue
            with open(path, "w") as f:
                f.write(update.updated_content)
            console.print(
                f"[dim cyan][skill_updater] updated {update.skill_name}/SKILL.md - {update.reason}[/dim cyan]"
            )

        if not result.updates:
            console.print("[dim cyan][skill_updater] no skill files needed updating this run[/dim cyan]")

        console.print(Panel(
            result.summary,
            title="[bold green]Skill Update Summary[/bold green]",
            border_style="green",
        ))

        return {
            "skill_update_summary": result.summary,
            "current_step":         "skill_updater_complete",
        }

    except Exception as e:
        console.print(f"[red][skill_updater] ERROR: {e}[/red]")
        # non-fatal - don't crash the workflow if skill update fails
        return {
            "skill_update_summary": f"skill_updater failed: {e}",
            "current_step":         "skill_updater_complete",
        }


# __ Graph _____________________________________________________________________

def route_orchestrator(state: AgentState) -> str:
    return state["next"]


graph = StateGraph(AgentState)

graph.add_node("orchestrator",  orchestrator)
graph.add_node("planner",       planner)
graph.add_node("installer",     installer)
graph.add_node("codegen",       codegen)
graph.add_node("executor",      executor)
graph.add_node("skill_updater", skill_updater)

graph.set_entry_point("orchestrator")

graph.add_conditional_edges("orchestrator", route_orchestrator, {
    "planner":   "planner",
    "installer": "installer",
    "codegen":   "codegen",
    "executor":  "executor",
    "end":       "skill_updater",   # always run skill_updater before END
})

graph.add_edge("planner",       "orchestrator")
graph.add_edge("installer",     "orchestrator")
graph.add_edge("codegen",       "executor")
graph.add_edge("executor",      "orchestrator")
graph.add_edge("skill_updater", END)

app = graph.compile()

# __ Run _______________________________________________________________________

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="MAW -- Multi-Agent Workflow")
    parser.add_argument("--paper", type=str, help="Path to the PDF paper or paper index (1-based)")
    parser.add_argument("--goal", type=str, help="Goal for the workflow")
    args = parser.parse_args()
    
    console.print(Panel("[bold blue]MAW -- Multi-Agent Workflow[/bold blue]", border_style="blue"))

    lit_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Literature")
    os.makedirs(lit_dir, exist_ok=True)

    pdfs = [f for f in os.listdir(lit_dir) if f.lower().endswith(".pdf")]
    if not pdfs:
        console.print("[red]No PDFs found in the Literature/ folder. Add a paper and try again.[/red]")
        raise SystemExit(1)

    console.print("\n[bold]Available papers:[/bold]")
    for i, name in enumerate(pdfs, 1):
        console.print(f"  {i}. {name}")

    # Handle paper selection from args or interactive input
    if args.paper:
        choice = args.paper
        # Try as index first, then as filename
        try:
            pdf_path = os.path.join(lit_dir, pdfs[int(choice) - 1])
        except (ValueError, IndexError):
            pdf_path = os.path.join(lit_dir, choice)
            if not os.path.isfile(pdf_path):
                console.print(f"[red]Paper not found: {choice}[/red]")
                raise SystemExit(1)
    else:
        choice = input("\nSelect a paper by number: ").strip()
        try:
            pdf_path = os.path.join(lit_dir, pdfs[int(choice) - 1])
        except (ValueError, IndexError):
            console.print("[red]Invalid selection.[/red]")
            raise SystemExit(1)

    console.print(f"[dim]Selected: {os.path.basename(pdf_path)}[/dim]")

    # Handle goal from args or interactive input
    if args.goal:
        goal = args.goal
    else:
        goal = input("\nDescribe your goal for this workflow: ").strip()
    
    if not goal:
        console.print("[red]Goal cannot be empty.[/red]")
        raise SystemExit(1)

    runs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs")
    os.makedirs(runs_dir, exist_ok=True)
    _run_log_path = os.path.join(runs_dir, datetime.now().strftime("%Y%m%d_%H%M%S") + ".jsonl")
    console.print(f"[dim]Run log: {_run_log_path}[/dim]")

    initial_state = {
        "messages":              [],
        "goal":                  goal,
        "pdf_path":              pdf_path,
        "literature_findings":   [],
        "stack_decision":        [],
        "tasks":                 [],
        "code_output":           [],
        "execution_output":      [],
        "dockerfile":            "",
        "dockerfile_approved":   False,
        "image_tag":             "",
        "current_step":          "start",
        "orchestrator_feedback": "",
        "next":                  "",
        "planner_revisions":     0,
        "installer_revisions":   0,
        "codegen_revisions":     0,
        "executor_revisions":    0,
        "skill_update_summary":  "",
    }

    app.invoke(initial_state)

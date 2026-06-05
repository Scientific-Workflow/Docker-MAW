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
    skill_requests:      list[str] = []  # e.g. ["use_cases/molecular_nucleation/orchestrator"]

class PlannerOutput(BaseModel):
    literature_findings: list[str]
    stack_decision:      list[str]
    tasks:               list[str]
    skill_requests:      list[str] = []  # e.g. ["use_cases/molecular_nucleation/planner", "systems/parsl"]

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
Your agent skill file contains your full operating instructions. Follow them.

Return ONLY a valid JSON object with exactly these keys:
- reasoning:           str — your analysis of the current state
- next:                "planner" | "installer" | "codegen" | "executor" | "end"
- feedback:            str — specific actionable feedback for the receiving agent, or "" if proceeding normally
- dockerfile_approved: bool — true ONLY when approving a pending Dockerfile, false in all other cases
- skill_requests:      list[str] — skill paths to load (first call only; empty on subsequent calls)
\
"""

PLANNER_PROMPT = """\
Your agent skill file contains your full operating instructions. Follow them.

Return ONLY a valid JSON object with exactly these keys:
- literature_findings: list[str] — specific, quantitative facts extracted from the paper
- stack_decision:      list[str] — packages available in the sandbox Dockerfile only
- tasks:               list[str] — ordered, Python-API-level implementation steps
- skill_requests:      list[str] — skill paths to load (first call only; empty on subsequent calls)

No markdown, no code fences, no explanation outside the JSON.

HANDLING ORCHESTRATOR FEEDBACK:
If the input ends with "Orchestrator feedback", fix every issue raised before returning.\
"""

# INSTALLER_PROMPT = """\
# You are a Docker environment specialist. Given a list of required software packages and scientific workflow context, generate a valid Dockerfile that installs all dependencies into an Ubuntu 22.04 container.

# Runtime environment: This container runs on a single developer machine NOT an HPC cluster. There is no MPI network fabric, no SLURM, and no multi-node communication. Prefer single-node, serial-friendly package configurations. Avoid HPC-specific MPI setups unless strictly required by the workflow.

# Return a JSON object with exactly one key:
# - dockerfile_content: string — the complete, valid Dockerfile

# The Dockerfile MUST:
# 1. Start with: FROM ubuntu:24.04
#    REASON: Ubuntu 24.04 ships glibc 2.39 and Python 3.12, both required by the OVITO ARM64 wheel.
#    Do NOT use ubuntu:22.04 — it has glibc 2.35 and Python 3.10, which are incompatible with OVITO on ARM64.
# 2. Set: ENV DEBIAN_FRONTEND=noninteractive
# 3. Install system dependencies INCLUDING the OpenMPI runtime shared library:
#    RUN apt-get update && apt-get install -y python3 python3-pip python3-dev build-essential wget git libosmesa6 libgl1 libglib2.0-0 libopenmpi3 && rm -rf /var/lib/apt/lists/*
#    NOTE: Ubuntu 24.04 renamed libgl1-mesa-glx to libgl1 and libosmesa6-dev to libosmesa6. Always use libgl1 and libosmesa6 — NOT libgl1-mesa-glx or libosmesa6-dev.
#    IMPORTANT: libopenmpi3 provides ONLY the shared library (.so file) that the pip lammps wheel links against at runtime.
#    It does NOT install mpirun or mpiexec. LAMMPS still runs in serial mode via the Python API — not via mpirun.
#    Without libopenmpi3, the pip lammps wheel will crash with "libmpi.so.12: cannot open shared object file".
# 4. Upgrade pip: RUN pip3 install --upgrade pip --break-system-packages
# 5. Install LAMMPS via pip wheel: RUN pip3 install lammps --break-system-packages
# 6. Install OVITO: RUN pip3 install ovito --break-system-packages
# 7. Install all other pip-installable packages from the provided stack list, each with --break-system-packages
# 8. Set working directory: WORKDIR /app
# 9. Set headless rendering env vars AND the OpenMPI binary path:
#    ENV LIBGL_ALWAYS_SOFTWARE=1
#    ENV PYOPENGL_PLATFORM=osmesa
#    ENV OVITO_GUI_MODE=0
#    ENV LD_LIBRARY_PATH=/usr/lib/aarch64-linux-gnu:/usr/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH
#    ENV PATH="/usr/lib/openmpi/bin:$PATH"
#    REASON: On Ubuntu 24.04, openmpi-bin installs orted to /usr/lib/openmpi/bin/ which is NOT in PATH by default.
#    The pip lammps wheel calls MPI_Init on load; ORTE's singleton mode searches PATH for orted and crashes if not found.

# Rules:
# - ALWAYS install libopenmpi3 AND openmpi-bin AND openmpi-common in the apt-get step.
#   libopenmpi3 provides the shared library; openmpi-bin provides the orted daemon; openmpi-common provides help files.
#   Without all three the pip lammps wheel crashes with MPI_Init failure on every run.
# - ALWAYS set ENV PATH="/usr/lib/openmpi/bin:$PATH" so ORTE can find orted at runtime.
# - Do NOT install libopenmpi-dev, libmpich-dev, or mpi4py
# - Do NOT build LAMMPS from source
# - Do NOT use conda or mamba
# - Always use --break-system-packages on every pip3 install — Ubuntu 24.04 requires it
# - Do NOT include markdown code fences or any text outside the JSON
# - Return ONLY a valid JSON object

# EXAMPLE OUTPUT:
# {
#   "dockerfile_content": "<complete valid Dockerfile as a single string with \\n line breaks>"
# }

# ---

# HANDLING ORCHESTRATOR FEEDBACK:
# If the input ends with a section titled "Orchestrator feedback", it means a previous version of your Dockerfile was rejected. You MUST:
# 1. Read every point in the feedback carefully — it may flag missing packages, wrong base image, incorrect syntax, or packages that caused runtime errors
# 2. Fix every issue raised — add missing packages, correct apt-get dependencies, adjust versions
# 3. Do not remove packages that were already correct — only fix what was flagged
# 4. Your revised Dockerfile will be reviewed again before the build runs

# Now apply this same structure to the stack and findings provided.\
# """

CODEGEN_PROMPT = """\
Your agent skill file contains your full operating instructions. Follow them.

Generate exactly two files. Return ONLY a valid JSON object:
{
  "files": [
    {"filename": "workflow.py",     "content": "<full file content>"},
    {"filename": "run_workflow.sh", "content": "<full file content>"}
  ]
}

No markdown, no code fences, no explanation outside the JSON.

HANDLING ORCHESTRATOR FEEDBACK:
If the input ends with "Orchestrator feedback", fix every issue raised.
Do not remove working logic — only fix what was flagged.
YOU MUST FIX THE ISSUE THE ORCHESTRATOR IDENTIFIES.\
"""

EXECUTOR_PROMPT = "TODO"

SKILL_UPDATER_PROMPT = """\
You are a meta-learning agent responsible for improving AI skill files based on what just happened in a workflow run.

You will be given:
- A summary of the run: revision counts, exit code, orchestrator feedback, errors from execution output
- The current content of each skill file that may need updating

Your job is to return an updated version of any skill files that need improvement.

RULES:
1. Preserve all existing YAML frontmatter, section headers, and document structure - do not reorder or rename sections.
2. Only add or modify content within sections - specifically pitfall tables, code examples, and rules lists.
3. Be specific - new pitfall entries must include the exact error message pattern or a recognizable excerpt.
4. No duplication - if a pitfall already exists, update it rather than adding a duplicate entry.
5. Only add things that actually happened in this run - do not invent pitfalls not evidenced in the state.
6. Every addition must be a concrete rule or code example, not a vague observation.
7. If no update is needed for a skill file, do not include it in the updates list.
8. updated_content must be the COMPLETE new file content - not a diff, not a partial excerpt.

Update guidance by skill type:
- agents/orchestrator: Update when routing decisions were wrong (high revision counts). Add routing pattern that failed + correct rule.
- agents/planner: Update when planner_revisions > 0. Add what extraction was wrong + correct approach.
- agents/codegen: Update when codegen_revisions > 0 or execution failed. Add the error pattern + fix.
- use_cases/molecular_nucleation/orchestrator: Update when LAMMPS-specific routing was wrong.
- use_cases/molecular_nucleation/planner: Update when stack_decision included wrong packages or tasks were insufficient.
- use_cases/molecular_nucleation/codegen: Update when LAMMPS/OVITO code produced errors. Add the exact fix.
- systems/parsl: Update when Parsl-specific errors appeared in execution_output.

skill_name must be the relative path without .SKILL.md extension (e.g. "agents/orchestrator", "use_cases/molecular_nucleation/codegen").

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

# __ Skill file helpers ________________________________________________________

_SKILLS_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")

def _read_skill(rel_path: str) -> str:
    """Read skills/<rel_path>.SKILL.md — returns '' if not found."""
    full = os.path.join(_SKILLS_ROOT, rel_path + ".SKILL.md")
    if os.path.isfile(full):
        with open(full) as f:
            return f.read()
    return ""

def _list_skills(folder: str) -> list:
    """List available names in skills/<folder>/: subdirectory names AND .SKILL.md base names."""
    d = os.path.join(_SKILLS_ROOT, folder)
    if not os.path.isdir(d):
        return []
    results = []
    for name in os.listdir(d):
        if os.path.isdir(os.path.join(d, name)):
            results.append(name)                  # e.g. "molecular_nucleation"
        elif name.endswith(".SKILL.md"):
            results.append(os.path.splitext(name)[0])  # e.g. "parsl"
    return results

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

    # Build system prompt: base skill + available skill index + core prompt
    _base = _read_skill("agents/orchestrator")
    _uc   = _list_skills("use_cases")
    _sys  = _list_skills("systems")
    _index = (f"\n\nAvailable skill contexts (set in skill_requests to load):"
              f"\n  use_cases: {_uc}  — request as \"use_cases/<name>/orchestrator\""
              f"\n  systems:   {_sys}  — request as \"systems/<name>\"") if (_uc or _sys) else ""
    _sys_prompt = (_base + _index + "\n\n---\n\n" + ORCHESTRATOR_SYSTEM_PROMPT) if _base else ORCHESTRATOR_SYSTEM_PROMPT
    _human = "\n\n".join(parts)

    result: OrchestratorOutput = _invoke_structured(model, OrchestratorOutput, [
        SystemMessage(content=_sys_prompt),
        HumanMessage(content=_human),
    ])

    # Two-pass: if sub-skills requested, load them and re-invoke once
    if result.skill_requests:
        _sub = "\n\n".join(filter(None, (_read_skill(r) for r in result.skill_requests)))
        if _sub:
            _enriched = _sys_prompt + f"\n\n=== Loaded Skills ===\n{_sub}\n\n(Final pass — do not set skill_requests.)"
            result = _invoke_structured(model, OrchestratorOutput, [
                SystemMessage(content=_enriched),
                HumanMessage(content=_human),
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

        # Build system prompt: base skill + available skill index + core prompt
        _base = _read_skill("agents/planner")
        _uc   = _list_skills("use_cases")
        _sys  = _list_skills("systems")
        _index = (f"\n\nAvailable skill contexts (set in skill_requests to load):"
                  f"\n  use_cases: {_uc}  — request as \"use_cases/<name>/planner\""
                  f"\n  systems:   {_sys}  — request as \"systems/<name>\"") if (_uc or _sys) else ""
        _sys_prompt = (_base + _index + "\n\n---\n\n" + PLANNER_PROMPT) if _base else PLANNER_PROMPT
        _human = f"Goal: {state['goal']}\n\nPaper:\n{pdf_text}{feedback_section}"

        result: PlannerOutput = _invoke_structured(model, PlannerOutput, [
            SystemMessage(content=_sys_prompt),
            HumanMessage(content=_human),
        ])

        # Two-pass: if sub-skills requested, load them and re-invoke once
        if result.skill_requests:
            _sub = "\n\n".join(filter(None, (_read_skill(r) for r in result.skill_requests)))
            if _sub:
                _enriched = _sys_prompt + f"\n\n=== Loaded Skills ===\n{_sub}\n\n(Final pass — do not set skill_requests.)"
                result = _invoke_structured(model, PlannerOutput, [
                    SystemMessage(content=_enriched),
                    HumanMessage(content=_human),
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

        # ── Early exit: if the sandbox image already exists, skip installer entirely ──
        _image_exists = subprocess.run(
            ["docker", "inspect", image_tag], capture_output=True,
        ).returncode == 0
        if _image_exists:
            console.print("[dim cyan][installer] sandbox image already exists — skipping installer[/dim cyan]")
            _existing = ""
            if os.path.isfile(dockerfile_path):
                with open(dockerfile_path) as _f:
                    _existing = _f.read()
            return {
                "dockerfile":   _existing,
                "image_tag":    image_tag,
                "current_step": "installer_complete",
            }

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
            # ── Phase 1: read pre-built Dockerfile from disk, send to orchestrator for approval ──
            console.print("\n[dim cyan][installer] reading pre-built Dockerfile from disk...[/dim cyan]")

            if not os.path.isfile(dockerfile_path):
                raise FileNotFoundError(
                    f"builds/Dockerfile not found at {dockerfile_path}. "
                    "Place a Dockerfile there before running the agent."
                )

            with open(dockerfile_path) as _f:
                dockerfile = _f.read()

            import re as _re

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

            # 3. Inject libmpi.so.12 symlink after libopenmpi3 install.
            #    The pip lammps wheel is compiled against libmpi.so.12, but
            #    Ubuntu 24.04 ships libmpi.so.40 via libopenmpi3. We create
            #    a symlink deterministically so LAMMPS can load at runtime.
            #    This is injected in code — not left to the LLM — so it is
            #    always present regardless of what the LLM generates.
            MPI_SYMLINK = (
                'RUN MPI_SO=$(find /usr/lib -name "libmpi.so.*" | grep -v cxx | sort | tail -1) && '
                'MPI_DIR=$(dirname "$MPI_SO") && '
                'ln -sf "$MPI_SO" "$MPI_DIR/libmpi.so.12" && '
                'ldconfig'
            )
            if 'libmpi.so.12' not in dockerfile:
                # Insert just before WORKDIR, or append before ENV block
                if 'WORKDIR' in dockerfile:
                    dockerfile = dockerfile.replace(
                        'WORKDIR',
                        MPI_SYMLINK + '\n\nWORKDIR',
                        1,
                    )
                else:
                    dockerfile = dockerfile + '\n' + MPI_SYMLINK + '\n'
                console.print("[dim cyan][installer] injected libmpi.so.12 symlink into Dockerfile[/dim cyan]")


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

        _base_codegen = _read_skill("agents/codegen")

        # Auto-load use-case skill from stack_decision (no skill_requests on CoderOutput)
        _stack = [p.lower() for p in state.get("stack_decision", [])]
        _uc_codegen = ""
        if any("lammps" in p for p in _stack):
            _uc_codegen = _read_skill("use_cases/molecular_nucleation/codegen")

        _codegen_prompt = _base_codegen or ""
        if _uc_codegen:
            _codegen_prompt += "\n\n---\n\n" + _uc_codegen
        _codegen_prompt += "\n\n---\n\n" + CODEGEN_PROMPT

        result: CoderOutput = _invoke_structured(coder_llm, CoderOutput, [
            SystemMessage(content=_codegen_prompt),
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
        image_tag   = state.get("image_tag") or "maw-sandbox:latest"

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
            "--data-dir",  "/app/data",
            "--work-dir",  "/app/work/run0",
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

        def skill_path(rel: str) -> str:
            return os.path.join(_SKILLS_ROOT, rel + ".SKILL.md")

        def read_skill(rel: str) -> str:
            return _read_skill(rel)

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
            f"Literature findings count: {len(state.get('literature_findings', []))}\n"
            f"Tasks count: {len(state.get('tasks', []))}\n"
            f"Stack decision: {state.get('stack_decision', [])}\n"
            f"Docker image tag: {state.get('image_tag') or 'maw-sandbox:latest'}\n"
            f"{exit_code_line}\n\n"
            f"Execution output (truncated):\n{execution_tail}"
        )

        # ── read current skill files ──────────────────────────────────────────
        skill_names = [
            "agents/orchestrator",
            "agents/planner",
            "agents/codegen",
            "use_cases/molecular_nucleation/orchestrator",
            "use_cases/molecular_nucleation/planner",
            "use_cases/molecular_nucleation/codegen",
            "systems/parsl",
        ]
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
    "end":       "skill_updater",
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

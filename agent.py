import os
import json
import operator
import subprocess
import warnings
from datetime import datetime
from typing import Annotated, Literal, Sequence
from typing_extensions import TypedDict
from pydantic import BaseModel
import fitz  # pymupdf
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
    data_files:            list[str]   # filenames present in data/ at run start
    literature_findings:   list[str]
    stack_decision:        dict   # structured env spec: base_image, apt_packages, pip_packages, special_installs, env_vars, workdir
    tasks:                 list[str]
    code_output:           Annotated[list[str], operator.add]
    execution_output:      Annotated[list[str], operator.add]
    environment_yml:       str   # content of builds/environment.yml
    install_script:        str   # content of builds/install.sh
    env_spec_approved:     bool
    conda_env_name:        str
    current_step:          str
    orchestrator_feedback: str
    next:                  str
    planner_revisions:     int
    installer_revisions:   int
    build_attempt:         int
    build_error:           str
    codegen_revisions:     int
    executor_revisions:    int
    skill_update_summary:  str    # summary written by skill_updater after the run

# __ Pydantic Schemas __________________________________________________________

class OrchestratorOutput(BaseModel):
    reasoning:          str
    next:               Literal["planner", "installer", "codegen", "executor", "end"]
    feedback:           str
    env_spec_approved:  bool = False
    skill_requests:     list[str] = []  # e.g. ["use_cases/molecular_nucleation/orchestrator"]

class SpecialInstall(BaseModel):
    name:   str
    reason: str

class StackDecision(BaseModel):
    base_image:        str
    apt_packages:      list[str]
    pip_packages:      list[str]
    special_installs:  list[SpecialInstall] = []
    env_vars:          dict[str, str] = {}
    workdir:           str = "."
    workflow_system:   str = "parsl"  # e.g. "parsl", "pycompss" — drives system skill loading in codegen

class PlannerOutput(BaseModel):
    literature_findings: list[str]
    stack_decision:      StackDecision
    tasks:               list[str]
    skill_requests:      list[str] = []  # e.g. ["knowledge/local_machine", "use_cases/molecular_nucleation/planner"]

class CodeFile(BaseModel):
    filename: str
    content:  str

class CoderOutput(BaseModel):
    files:           list[CodeFile]
    needs_more_info: bool = False
    missing_info:    str  = ""

class InstallerOutput(BaseModel):
    environment_yml: str
    install_script:  str   # bash script content — empty string if no special installs

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
- reasoning:          str — your analysis of the current state
- next:               "planner" | "installer" | "codegen" | "executor" | "end"
- feedback:           str — specific actionable feedback for the receiving agent, or "" if proceeding normally
- env_spec_approved:  bool — true ONLY when approving a pending environment spec, false in all other cases
- skill_requests:     list[str] — skill paths to load (first call only; empty on subsequent calls)
\
"""

PLANNER_PROMPT = """\
Your agent skill file contains your full operating instructions. Follow them.

Return ONLY a valid JSON object with exactly these keys:
- literature_findings: list[str] — specific, quantitative facts extracted from the paper
- stack_decision:      object — complete environment specification with fields: base_image (str), apt_packages (list[str]), pip_packages (list[str]), special_installs (list of {name, reason}), env_vars (dict), workdir (str), workflow_system (str, default "parsl" — set to "pycompss" if the paper uses PyCOMPSs)
- tasks:               list[str] — ordered, Python-API-level implementation steps
- skill_requests:      list[str] — skill paths to load (first call only; empty on subsequent calls)

stack_decision MUST be a structured object — not a flat list of package names.
Read the goal to determine the target runtime environment, then load the matching knowledge skill.

No markdown, no code fences, no explanation outside the JSON.

HANDLING ORCHESTRATOR FEEDBACK:
If the input ends with "Orchestrator feedback", fix every issue raised before returning.\
"""


INSTALLER_PROMPT = """\
Your agent skill file contains your full operating instructions. Follow them.

You will be given a stack_decision specification produced by the planner. Generate a conda environment.yml and an install.sh script that implement every field in the specification.

Return ONLY a valid JSON object with exactly two keys:
- environment_yml: str — the complete environment.yml as a single string with \\n line breaks
- install_script:  str — the complete install.sh as a single string with \\n line breaks (empty string "" if no special_installs)

environment.yml rules:
- name: maw_sandbox
- channels: [conda-forge, defaults]
- Always include python=3.11
- Translate apt_packages to conda-forge equivalents:
    cmake → cmake
    build-essential → gcc, gxx_linux-64, make (or gcc_linux-64 on Linux)
    libfftw3-dev → fftw
    libpng-dev → libpng
    libjpeg-dev → libjpeg-turbo
    zlib1g-dev → zlib
    libosmesa6 → mesalib
    libgl1 / libegl1 / libopengl0 → mesa-libgl-devel-cos6-x86_64 OR leave to system (OSMesa is in mesalib)
    libglib2.0-0 → glib
    libxkbcommon0 → xkeyboard-config
    libdbus-1-3 → dbus
    wget → wget
    git → git
    python3-dev → already satisfied by python=3.11
- Include all pip_packages under a pip: subsection in dependencies
- base_image and workdir are IGNORED — installer writes only environment.yml and install.sh
- Do NOT add packages not in stack_decision

install.sh rules:
- Start with: #!/bin/bash\\nset -e
- Use $CONDA_PREFIX for install prefix and library paths (it is set inside conda run)
- For LAMMPS source builds: download tarball → cmake with -DCMAKE_INSTALL_PREFIX=$CONDA_PREFIX -DCMAKE_PREFIX_PATH=$CONDA_PREFIX → make -j$(nproc) → make install → pip install python binding
- For any pip install inside install.sh: use pip install (no --break-system-packages needed in conda env)
- Set LD_LIBRARY_PATH=$CONDA_PREFIX/lib after building source libraries
- Return empty string "" if stack_decision has no special_installs

HANDLING ORCHESTRATOR FEEDBACK:
If the input ends with "Orchestrator feedback", fix every issue raised. Do not remove packages that were already correct.\
"""

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
Repo directory tree — host filesystem. All paths are real host paths:

<repo_root>/                           ← os.path.dirname(os.path.abspath(__file__))
├── agent.py
├── requirements.txt
├── .env
├── data/
│   └── <user data files>
├── Literature/
│   └── *.pdf
├── work/
│   └── run_YYYYMMDD_HHMMSS/           ← per-run output directory (created by executor)
│       ├── data/                      ← staged copy of user-selected data files
│       └── <workflow output files>
└── builds/                            ← ALL generated files land here
    ├── environment.yml                ← conda env spec (generated by installer)
    ├── install.sh                     ← special installs script (generated by installer)
    ├── workflow.py                    ← workflow script (generated by codegen)
    └── run_workflow.sh                ← launcher script (generated by codegen)

The workflow runs natively via conda run. Paths in workflow.py are real host paths
passed as command-line arguments:
  --data-dir  absolute host path to the staged data dir for this run
  --work-dir  absolute host path to the run output directory

workflow.py MUST accept --data-dir and --work-dir as argparse arguments and use them
for ALL file I/O. Do NOT hardcode any absolute paths — use --data-dir and --work-dir.

run_workflow.sh must use conda run to execute the workflow. Resolve its own absolute
path with BASH_SOURCE — never use pwd. Example:
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  REPO_DIR="$(dirname "$SCRIPT_DIR")"
  conda run -n maw_sandbox --no-capture-output python3 "$SCRIPT_DIR/workflow.py" \\
    --data-dir "$REPO_DIR/work/run_YYYYMMDD_HHMMSS/data" \\
    --work-dir "$REPO_DIR/work/run_YYYYMMDD_HHMMSS"\
"""

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
        with open(full, encoding="utf-8") as f:
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
        sd = state["stack_decision"]
        if isinstance(sd, dict):
            parts.append(
                f"Stack decision:\n"
                f"  base_image: {sd.get('base_image','?')}\n"
                f"  apt_packages: {sd.get('apt_packages', [])}\n"
                f"  pip_packages: {sd.get('pip_packages', [])}\n"
                f"  special_installs: {[s.get('name') for s in sd.get('special_installs', [])]}\n"
                f"  env_vars: {sd.get('env_vars', {})}\n"
                f"  workdir: {sd.get('workdir', '?')}"
            )
        else:
            parts.append(f"Stack: {sd}")
    if state.get("tasks"):
        parts.append(f"Tasks ({len(state['tasks'])}):\n" +
                     "\n".join(f"  {i+1}. {t}" for i, t in enumerate(state["tasks"])))
    if state.get("environment_yml"):
        spec_display = f"environment.yml:\n{state['environment_yml']}"
        if state.get("install_script"):
            spec_display += f"\n\ninstall.sh (first 40 lines):\n" + "\n".join(state["install_script"].splitlines()[:40])
        parts.append(spec_display)
    if state.get("code_output"):
        parts.append(f"Code output (latest):\n{state['code_output'][-1]}")
    if state.get("execution_output") and state.get("current_step") != "installer_env_spec_pending_approval":
        parts.append(f"Execution output (latest):\n{state['execution_output'][-1]}")
    if state.get("build_error"):
        parts.append(f"Build error (attempt {state.get('build_attempt', 0)}):\n{state['build_error']}")

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
    if state.get("current_step") == "installer_env_spec_pending_approval":
        result.next = "installer"
    elif state.get("current_step") == "installer_complete":
        result.next = "codegen"
    elif state.get("current_step") == "installer_build_failed":
        attempt = state.get("build_attempt", 0)
        console.print(f"[yellow][orchestrator] conda env build failed (attempt {attempt}) — sending back to installer[/yellow]")
        result.next = "installer"
        if not result.feedback:
            result.feedback = f"The conda environment build failed. Error output:\n{state.get('build_error', '')}\nFix the environment.yml or install.sh to resolve this error."

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
        "installer": bool(state.get("environment_yml")),
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
        "env_spec_approved":     result.env_spec_approved,
        "current_step":          f"orchestrator_routed_to_{result.next}",
        **revision_update,
    }


def planner(state: AgentState) -> dict:
    try:
        console.print("\n[dim cyan][planner] reading PDF...[/dim cyan]")

        doc      = fitz.open(state["pdf_path"])
        pdf_text = "\n".join(page.get_text() for page in doc)
        console.print(f"[dim cyan][planner] loaded {len(doc)} pages[/dim cyan]")
        doc.close()

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
        _data_files = state.get("data_files", [])
        _src_data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        _expanded_files = []
        for _name in _data_files:
            _full = os.path.join(_src_data_dir, _name)
            if os.path.isdir(_full):
                for _root, _, _fnames in os.walk(_full):
                    for _fn in sorted(_fnames):
                        _rel = os.path.relpath(os.path.join(_root, _fn), _src_data_dir)
                        _expanded_files.append(_rel.replace(os.sep, "/"))
            else:
                _expanded_files.append(_name)
        _files_section = (f"\n\nAvailable data files (passed as --data-dir to workflow): {', '.join(_expanded_files)}"
                          if _expanded_files else "")
        _human = f"Goal: {state['goal']}{_files_section}\n\nPaper:\n{pdf_text}{feedback_section}"

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

        sd = result.stack_decision
        findings  = "\n".join(f"  [cyan]•[/cyan] {f}" for f in result.literature_findings)
        stack_lines = [
            f"  base_image: {sd.base_image}",
            f"  apt_packages ({len(sd.apt_packages)}): {', '.join(sd.apt_packages)}",
            f"  pip_packages: {', '.join(sd.pip_packages)}",
        ]
        if sd.special_installs:
            stack_lines.append(f"  special_installs: {', '.join(s.name for s in sd.special_installs)}")
        if sd.env_vars:
            stack_lines.append(f"  env_vars: {', '.join(sd.env_vars.keys())}")
        stack = "\n".join(stack_lines)
        tasks = "\n".join(f"  [bold]{i+1}.[/bold] {t}" for i, t in enumerate(result.tasks))
        console.print(Panel(
            f"[bold]Literature Findings[/bold]\n{findings}\n\n"
            f"[bold]Stack Decision[/bold]\n{stack}\n\n"
            f"[bold]Tasks[/bold]\n{tasks}",
            title="[bold green]Planner Output[/bold green]",
            border_style="green",
        ))

        return {
            "literature_findings": result.literature_findings,
            "stack_decision":      result.stack_decision.model_dump(),
            "tasks":               result.tasks,
            "current_step":        "planner_complete",
        }
    except Exception as e:
        console.print(f"[red][planner] ERROR: {e}[/red]")
        raise


def installer(state: AgentState) -> dict:
    try:
        import hashlib, shutil, sys as _sys
        build_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "builds")
        os.makedirs(build_dir, exist_ok=True)
        env_yml_path    = os.path.join(build_dir, "environment.yml")
        install_sh_path = os.path.join(build_dir, "install.sh")
        conda_env_name  = state.get("conda_env_name") or "maw_sandbox"

        if state.get("env_spec_approved"):
            # ── Phase 2: spec approved — create/update conda env ─────────────
            conda_bin = shutil.which("conda") or os.environ.get("CONDA_EXE")
            if not conda_bin:
                console.print(
                    "[yellow][installer] conda not found in PATH. "
                    "Skipping env build — files written. "
                    f"Run 'conda env create -n {conda_env_name} -f builds/environment.yml' to build.[/yellow]"
                )
                return {"current_step": "installer_complete"}

            # ── skip rebuild if spec unchanged and env already exists ─────────
            hash_file = os.path.join(build_dir, ".env_spec_hash")
            with open(env_yml_path) as f:
                _yml_content = f.read()
            _install_content = ""
            if os.path.isfile(install_sh_path):
                with open(install_sh_path) as f:
                    _install_content = f.read()
            current_hash = hashlib.md5((_yml_content + _install_content).encode()).hexdigest()

            env_exists = conda_env_name in subprocess.run(
                [conda_bin, "env", "list"], capture_output=True, text=True
            ).stdout

            stored_hash = ""
            if os.path.isfile(hash_file):
                with open(hash_file) as f:
                    stored_hash = f.read().strip()

            if current_hash == stored_hash and env_exists:
                console.print(f"[dim cyan][installer] env spec unchanged and '{conda_env_name}' exists — skipping rebuild[/dim cyan]")
                return {"current_step": "installer_complete"}

            # ── create or update env ──────────────────────────────────────────
            if env_exists:
                console.print(f"[dim cyan][installer] updating conda env '{conda_env_name}'...[/dim cyan]")
                cmd_env = [conda_bin, "env", "update", "-n", conda_env_name, "--file", env_yml_path, "--prune"]
            else:
                console.print(f"[dim cyan][installer] creating conda env '{conda_env_name}' (this may take several minutes)...[/dim cyan]")
                cmd_env = [conda_bin, "env", "create", "-n", conda_env_name, "--file", env_yml_path]

            _build_lines = []
            proc = subprocess.Popen(cmd_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for _line in proc.stdout:
                _sys.stdout.write(_line)
                _sys.stdout.flush()
                _build_lines.append(_line)
            proc.wait()

            if proc.returncode != 0:
                _error_tail = "".join(_build_lines[-80:])
                console.print(f"[red][installer] conda env create failed (exit {proc.returncode}) — returning to orchestrator[/red]")
                return {
                    "current_step":    "installer_build_failed",
                    "build_error":     f"conda env create exit {proc.returncode}. Last output:\n{_error_tail}",
                    "build_attempt":   state.get("build_attempt", 0) + 1,
                    "env_spec_approved": False,
                }

            # ── run install.sh inside the new env (special installs) ──────────
            install_content = state.get("install_script", "").strip()
            if install_content and install_content not in ("", "#!/bin/bash\nset -e"):
                console.print(f"[dim cyan][installer] running install.sh in conda env '{conda_env_name}'...[/dim cyan]")
                _sh_lines = []
                sh_proc = subprocess.Popen(
                    [conda_bin, "run", "-n", conda_env_name, "--no-capture-output", "bash", install_sh_path],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                )
                for _line in sh_proc.stdout:
                    _sys.stdout.write(_line)
                    _sys.stdout.flush()
                    _sh_lines.append(_line)
                sh_proc.wait()

                if sh_proc.returncode != 0:
                    _error_tail = "".join(_sh_lines[-80:])
                    console.print(f"[red][installer] install.sh failed (exit {sh_proc.returncode}) — returning to orchestrator[/red]")
                    return {
                        "current_step":    "installer_build_failed",
                        "build_error":     f"install.sh exit {sh_proc.returncode}. Last output:\n{_error_tail}",
                        "build_attempt":   state.get("build_attempt", 0) + 1,
                        "env_spec_approved": False,
                    }

            with open(hash_file, "w") as f:
                f.write(current_hash)

            console.print(f"[dim cyan][installer] conda env '{conda_env_name}' ready[/dim cyan]")
            return {"current_step": "installer_complete"}

        else:
            # ── Phase 1: generate environment.yml + install.sh via LLM ───────
            console.print("\n[dim cyan][installer] generating conda env spec from stack_decision...[/dim cyan]")

            stack = state.get("stack_decision", {})
            if not stack:
                raise ValueError("stack_decision is empty — planner must run before installer")

            feedback = state.get("orchestrator_feedback", "")
            feedback_section = (f"\n\nOrchestrator feedback — fix these issues in your revised spec:\n{feedback}"
                                if feedback else "")

            _base = _read_skill("agents/installer")
            _special_names = [s.get("name", "").lower() for s in stack.get("special_installs", [])]
            _pip_names      = [p.lower() for p in stack.get("pip_packages", [])]
            _uc = ""
            if any("lammps" in x for x in _special_names + _pip_names):
                _uc = _read_skill("use_cases/molecular_nucleation/installer")
            _sys_prompt = _base
            if _uc:
                _sys_prompt += "\n\n---\n\n" + _uc
            _sys_prompt += "\n\n---\n\n" + INSTALLER_PROMPT

            _human = (
                f"stack_decision:\n{json.dumps(stack, indent=2)}"
                f"{feedback_section}"
            )

            result: InstallerOutput = _invoke_structured(coder_llm, InstallerOutput, [
                SystemMessage(content=_sys_prompt),
                HumanMessage(content=_human),
            ])

            env_yml    = result.environment_yml.strip()
            install_sh = result.install_script.strip()

            # Ensure install.sh has a proper shebang + set -e header
            if install_sh and not install_sh.startswith("#!"):
                install_sh = "#!/bin/bash\nset -e\n" + install_sh

            with open(env_yml_path, "w") as f:
                f.write(env_yml)
            if install_sh:
                with open(install_sh_path, "w") as f:
                    f.write(install_sh)
            elif os.path.isfile(install_sh_path):
                os.remove(install_sh_path)

            display = env_yml
            if install_sh:
                display += f"\n\n--- install.sh ---\n{install_sh}"
            console.print(Panel(
                display,
                title="[bold yellow]Conda Env Spec — Pending Orchestrator Approval[/bold yellow]",
                border_style="yellow",
            ))

            return {
                "environment_yml": env_yml,
                "install_script":  install_sh,
                "current_step":    "installer_env_spec_pending_approval",
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

        conda_env = state.get("conda_env_name") or "maw_sandbox"

        context = (
            f"Project layout:\n{PROJECT_LAYOUT}" +
            "\n\nLiterature findings:\n" + "\n".join(f"  - {f}" for f in state.get("literature_findings", [])) +
            "\n\nTasks to implement:\n" + "\n".join(f"  {i+1}. {t}" for i, t in enumerate(state.get("tasks", []))) +
            f"\n\nConda environment name: {conda_env}" +
            feedback_section
        )

        _base_codegen = _read_skill("agents/codegen")

        _sd = state.get("stack_decision", {})
        _special_names = [s.get("name", "").lower() for s in _sd.get("special_installs", [])]
        _pip_names     = [p.lower() for p in _sd.get("pip_packages", [])]

        # Auto-load system skill from workflow_system (e.g. systems/parsl, systems/pycompss)
        _workflow_system = _sd.get("workflow_system", "parsl").lower()
        _sys_codegen = _read_skill(f"systems/{_workflow_system}") if _workflow_system else ""

        # Auto-load use-case skill from stack_decision packages
        _uc_codegen = ""
        if any("lammps" in x for x in _special_names + _pip_names):
            _uc_codegen = _read_skill("use_cases/molecular_nucleation/codegen")

        # Layer order: base → system → use-case → core prompt
        _codegen_prompt = _base_codegen or ""
        if _sys_codegen:
            _codegen_prompt += "\n\n---\n\n" + _sys_codegen
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
        import shutil as _shutil
        console.print("\n[dim cyan][executor] running workflow via conda run...[/dim cyan]")

        repo_dir    = os.path.dirname(os.path.abspath(__file__))
        build_dir   = os.path.join(repo_dir, "builds")
        workflow_py = os.path.join(build_dir, "workflow.py")
        conda_env   = state.get("conda_env_name") or "maw_sandbox"
        conda_bin   = _shutil.which("conda")

        if not conda_bin:
            msg = (
                "conda not found in PATH. "
                f"To execute manually: conda run -n {conda_env} python3 builds/workflow.py ..."
            )
            console.print(f"[yellow][executor] {msg}[/yellow]")
            return {
                "execution_output": [f"SKIPPED (no conda): {msg}"],
                "current_step":     "executor_complete",
            }

        if not os.path.isfile(workflow_py):
            raise FileNotFoundError(f"workflow.py not found at {workflow_py}. Run codegen first.")

        # Unique timestamped run directory — never overwrites previous results
        run_id       = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir_name = f"run_{run_id}"
        work_dir     = os.path.join(repo_dir, "work", run_dir_name)
        os.makedirs(work_dir, exist_ok=True)

        # Stage user-selected data files into a per-run data subdirectory
        data_staging = os.path.join(work_dir, "data")
        os.makedirs(data_staging, exist_ok=True)
        src_data_dir = os.path.join(repo_dir, "data")
        for _fname in state.get("data_files", []):
            _src = os.path.join(src_data_dir, _fname)
            if os.path.isdir(_src):
                _shutil.copytree(_src, os.path.join(data_staging, _fname), dirs_exist_ok=True)
            elif os.path.isfile(_src):
                _shutil.copy2(_src, os.path.join(data_staging, _fname))
        console.print(f"[dim cyan][executor] staged data files: {state.get('data_files', [])}[/dim cyan]")

        # Build subprocess env: inherit everything, then overlay stack_decision env_vars
        _run_env = os.environ.copy()
        for _k, _v in state.get("stack_decision", {}).get("env_vars", {}).items():
            _run_env[_k] = _v

        cmd = [
            conda_bin, "run", "-n", conda_env, "--no-capture-output",
            "python3", workflow_py,
            "--data-dir", data_staging,
            "--work-dir", work_dir,
        ]

        console.print(f"[dim cyan][executor] run directory: work/{run_dir_name}[/dim cyan]")
        console.print(f"[dim cyan][executor] command: {' '.join(cmd)}[/dim cyan]")
        console.print("[dim cyan][executor] this may take several minutes...[/dim cyan]")

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,
            env=_run_env,
        )

        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        combined = (
            f"=== EXIT CODE: {proc.returncode} ===\n\n"
            f"=== STDOUT ===\n{stdout}\n\n"
            f"=== STDERR ===\n{stderr}"
        )

        metadata = {
            "run_id":         run_id,
            "goal":           state.get("goal", ""),
            "paper":          state.get("pdf_path", ""),
            "timestamp":      datetime.now().isoformat(),
            "exit_code":      proc.returncode,
            "conda_env":      conda_env,
            "stack_decision": state.get("stack_decision", {}),
            "revisions": {
                "planner":   state.get("planner_revisions",   0),
                "installer": state.get("installer_revisions", 0),
                "codegen":   state.get("codegen_revisions",   0),
                "executor":  state.get("executor_revisions",  0),
            },
        }
        with open(os.path.join(work_dir, "run_metadata.json"), "w") as f:
            json.dump(metadata, f, indent=2)

        color = "green" if proc.returncode == 0 else "red"
        console.print(Panel(
            combined[:6000],
            title=f"[bold {color}]Executor Output (exit {proc.returncode})[/bold {color}]",
            border_style=color,
        ))

        if proc.returncode != 0:
            console.print(f"[red][executor] workflow exited with code {proc.returncode}[/red]")
        else:
            console.print(f"[dim cyan][executor] workflow completed successfully — outputs in work/{run_dir_name}[/dim cyan]")

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
            f"Stack decision: {json.dumps(state.get('stack_decision', {}), indent=2)}\n"
            f"Conda env: {state.get('conda_env_name') or 'maw_sandbox'}\n"
            f"{exit_code_line}\n\n"
            f"Execution output (truncated):\n{execution_tail}"
        )

        # ── read current skill files ──────────────────────────────────────────
        # Collect all knowledge skill files dynamically so new environment skills are picked up automatically
        _knowledge_skills = [
            f"knowledge/{os.path.splitext(f)[0].replace('.SKILL', '')}"
            for f in os.listdir(os.path.join(_SKILLS_ROOT, "knowledge"))
            if f.endswith(".SKILL.md")
        ] if os.path.isdir(os.path.join(_SKILLS_ROOT, "knowledge")) else []

        skill_names = [
            "agents/orchestrator",
            "agents/planner",
            "agents/installer",
            "agents/codegen",
            *_knowledge_skills,
            "use_cases/molecular_nucleation/orchestrator",
            "use_cases/molecular_nucleation/planner",
            "use_cases/molecular_nucleation/installer",
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

        # ── HITL: show proposed updates and ask before writing ───────────────
        approved_updates = []
        if not result.updates:
            console.print("[dim cyan][skill_updater] no skill files needed updating this run[/dim cyan]")
        else:
            console.print(Panel(
                "\n".join(
                    f"  [bold]{u.skill_name}[/bold]\n    {u.reason}"
                    for u in result.updates
                ),
                title="[bold yellow]Skill Updater — Proposed Changes[/bold yellow]",
                border_style="yellow",
            ))
            try:
                choice = input(
                    "\nApply skill updates? [y = all / n = none / r = review each]: "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                choice = "n"
                console.print("[yellow][skill_updater] non-interactive — skipping updates[/yellow]")

            if choice == "y":
                approved_updates = result.updates
            elif choice == "r":
                for update in result.updates:
                    console.print(f"\n  [bold]{update.skill_name}[/bold]: {update.reason}")
                    try:
                        ans = input("  Apply this update? [y/n]: ").strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        ans = "n"
                    if ans == "y":
                        approved_updates.append(update)
            else:
                console.print("[dim cyan][skill_updater] updates skipped by user[/dim cyan]")

        # ── write approved updates ────────────────────────────────────────────
        for update in approved_updates:
            path = skill_path(update.skill_name)
            if not os.path.isfile(path):
                console.print(f"[yellow][skill_updater] skipping unknown skill: {update.skill_name}[/yellow]")
                continue
            with open(path, "w") as f:
                f.write(update.updated_content)
            console.print(
                f"[dim cyan][skill_updater] updated {update.skill_name} — {update.reason}[/dim cyan]"
            )

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

    # Data file selection
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(data_dir, exist_ok=True)
    all_data_items = sorted(os.listdir(data_dir))

    if not all_data_items:
        console.print("[yellow]No files found in data/ — continuing with empty data directory.[/yellow]")
        data_files = []
    else:
        console.print("\n[bold]Available data files/folders:[/bold]")
        for i, name in enumerate(all_data_items, 1):
            suffix = "/" if os.path.isdir(os.path.join(data_dir, name)) else ""
            console.print(f"  {i}. {name}{suffix}")
        console.print(f"  a. All")

        raw = input("\nSelect files/folders to expose (e.g. 1,2,3 or 'a' for all): ").strip().lower()
        if raw == "a" or raw == "":
            data_files = all_data_items
        else:
            selected = []
            for token in raw.replace(",", " ").split():
                try:
                    idx = int(token) - 1
                    if 0 <= idx < len(all_data_items):
                        selected.append(all_data_items[idx])
                    else:
                        console.print(f"[yellow]Skipping out-of-range index: {token}[/yellow]")
                except ValueError:
                    console.print(f"[yellow]Skipping unrecognized input: {token}[/yellow]")
            data_files = selected

        if not data_files:
            console.print("[yellow]No items selected — continuing with empty data directory.[/yellow]")

    console.print(f"[dim]Exposing: {', '.join(data_files) if data_files else 'none'}[/dim]")

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

    initial_state = {
        "messages":              [],
        "goal":                  goal,
        "pdf_path":              pdf_path,
        "data_files":            data_files,
        "literature_findings":   [],
        "stack_decision":        {},
        "tasks":                 [],
        "code_output":           [],
        "execution_output":      [],
        "environment_yml":       "",
        "install_script":        "",
        "env_spec_approved":     False,
        "conda_env_name":        "maw_sandbox",
        "current_step":          "start",
        "orchestrator_feedback": "",
        "next":                  "",
        "planner_revisions":     0,
        "installer_revisions":   0,
        "codegen_revisions":     0,
        "executor_revisions":    0,
        "skill_update_summary":  "",
        "build_attempt":         0,
        "build_error":           "",
    }

    app.invoke(initial_state)

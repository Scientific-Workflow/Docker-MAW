---
name: auto_update
description: Use when improving, updating, or rewriting skill files based on agent run history, orchestrator feedback, revision counts, or execution errors. Covers how to analyze AgentState, what to extract from conversation history, how to update codegen/SKILL.md and parsl/SKILL.md, and how the skill_updater node in agent.py works. Trigger keywords: auto_update, skill_updater, improve skill, update skill file, skill file improvement, rewrite skill.
---

# Auto-Update Skill — Living Knowledge Base (Docker-MAW)

This skill governs how the `skill_updater` node in `agent.py` automatically improves
skill files after each workflow run, using the accumulated `AgentState` as its source of truth.

---

## 1. Core Idea

Skill files are the agent's **long-term memory**. Each workflow run produces signals —
orchestrator feedback, revision counts, execution errors, and working patterns — that
reveal gaps or inaccuracies in the current skill files. The `skill_updater` node reads
those signals and rewrites the skill files to be more accurate for the next run.

```
workflow run completes
        │
        ▼
skill_updater node
        │
        ├── reads AgentState (feedback, revisions, code_output, execution_output)
        ├── identifies which skill files need updating
        ├── generates improved content via LLM
        └── writes updated SKILL.md files to disk
        │
        ▼
END
```

---

## 2. When to Trigger an Update

The `skill_updater` node runs **after executor**, just before `END`. It always runs,
but the depth of the update depends on what happened:

| Signal | Action |
|---|---|
| `codegen_revisions > 0` | Update `codegen/SKILL.md` — add the pitfall that caused the revision |
| `installer_revisions > 0` | Update `codegen/SKILL.md` — note the Dockerfile issue that affected codegen |
| Dockerfile rejected in phase 1 (orchestrator set `dockerfile_approved=False` with feedback) | Update `codegen/SKILL.md` — note what the installer got wrong |
| `executor` returned non-zero exit | Update `codegen/SKILL.md` — add the runtime error pattern and fix |
| `planner_revisions > 0` | Update `codegen/SKILL.md` — note what context was missing from tasks |
| Parsl-specific error in execution_output | Update `parsl/SKILL.md` — add the error pattern to pitfalls |
| Docker-specific error in execution_output | Update `codegen/SKILL.md` — add the Docker runtime issue |
| Clean run (0 revisions, exit 0) | Light update — reinforce what worked (patterns, API calls, config) |

---

## 3. What to Extract from AgentState

```python
# Key fields to analyze
state["orchestrator_feedback"]   # last feedback string — most direct signal
state["codegen_revisions"]       # how many times codegen was rejected
state["installer_revisions"]     # how many times installer was rejected
state["dockerfile"]              # the Dockerfile that was generated
state["dockerfile_approved"]     # whether it was approved
state["code_output"]             # list of all generated code versions (accumulated)
state["execution_output"]        # list of all execution results (accumulated)
state["literature_findings"]     # what the planner extracted
state["tasks"]                   # what tasks were given to codegen
state["stack_decision"]          # what packages were required
state["image_tag"]               # Docker image tag used
```

**Extraction rules:**
- If `codegen_revisions > 1`, compare `code_output[0]` vs `code_output[-1]` — the diff reveals what was wrong and how it was fixed.
- Scan `execution_output[-1]` for: `ImportError`, `ModuleNotFoundError`, `FileNotFoundError`, `RuntimeError`, `TypeError`, Docker errors (`Error response from daemon`, `cannot find image`, `permission denied`), Parsl errors (`NoDataFlowKernelError`, serialization failures), MPI errors (`libmpi.so.12: cannot open shared object`).
- Scan `orchestrator_feedback` for recurring themes.
- A clean run (`codegen_revisions == 0`, exit code 0) is informative — extract working patterns and reinforce them.

---

## 4. How to Update Each Skill File

### 4.1 `codegen/SKILL.md`

Update when codegen needed revisions, Dockerfile was rejected, or execution failed.

**What to add/modify:**
- New rows in the **Common Pitfalls** table (Section 10) for any error that caused a revision
- New code examples in Section 5 if a better API pattern was discovered
- Update the **New workflow checklist** (Section 13) if a new required step was identified
- Update Section 8 (Dockerfile Rules) if a new Dockerfile requirement was found
- Add a note to Section 9 (Orchestrator Feedback Handling) if a feedback pattern recurred

**What NOT to change:**
- The Pydantic schemas (Section 3) — only change if the schema actually changed in `agent.py`
- The graph architecture (Section 1) — only change if nodes were added/removed
- The ownership rules (Section 0) — never modify these
- Working code examples that produced a successful run

### 4.2 `parsl/SKILL.md`

Update when execution output contains Parsl-specific errors.

**What to add/modify:**
- New rows in Section 7 (Common Pitfalls) for any Parsl error encountered
- Config corrections in Section 3 if the LocalProvider config caused issues
- API corrections in Section 8 if an AppFuture method was used incorrectly

### 4.3 `auto_update/SKILL.md` (this file)

Update if the update logic itself changed — e.g., new signals to watch for, new skill files
added to the repo, or changes to what `AgentState` fields are available.

---

## 5. Update Quality Rules

When generating updated skill file content, the LLM MUST follow these rules:

1. **Preserve structure** — keep all existing section numbers and headers. Only add or modify content within sections; do not reorder or rename sections.
2. **Be specific** — new pitfall rows must include the exact error message pattern (or a recognizable excerpt).
3. **No duplication** — if a pitfall already exists, update it rather than adding a duplicate row.
4. **Cite the evidence** — updates must be traceable to something in `AgentState` (e.g., "observed in execution_output: `libmpi.so.12: cannot open shared object`").
5. **Do not hallucinate** — only add things that actually happened in the run.
6. **Keep it actionable** — every addition must be a concrete rule or code example.
7. **Preserve ownership rules** — never modify Section 0 of `codegen/SKILL.md`.

---

## 6. SkillUpdaterOutput Schema

```python
class SkillFileUpdate(BaseModel):
    skill_name:      str   # e.g. "codegen", "parsl", "auto_update"
    updated_content: str   # full new content of the SKILL.md file
    reason:          str   # one-sentence explanation of what changed and why

class SkillUpdaterOutput(BaseModel):
    updates: list[SkillFileUpdate]  # may be empty if no updates needed
    summary: str                    # overall summary of what was learned from this run
```

---

## 7. skill_updater Node Behavior

```python
def skill_updater(state: AgentState) -> dict:
    # 1. Collect signals from AgentState
    # 2. Build context string summarizing what happened
    # 3. Read current content of each skill file
    # 4. Send to LLM (model, not coder_llm) with update rules
    # 5. Write updated content back to disk via open()
    # 6. Return summary to state
    return {
        "skill_update_summary": result.summary,
        "current_step":         "skill_updater_complete",
    }
```

- Uses `model` (not `coder_llm`) — the update task is analytical.
- Uses `_invoke_structured()` consistent with the rest of the codebase.
- Runs unconditionally after executor — non-fatal (errors are caught and logged, workflow proceeds to END).
- Writes files directly via Python `open()` — no subprocess.

---

## 8. Base + Live Pattern

Each skill file IS the live version. To protect against bad updates:

- The node sends the full existing content to the LLM and asks for an improved version — not a rewrite from scratch.
- If `updates` is empty, no files are touched.
- The `reason` field is printed to console so you can see what changed.

**Manual reset** if an auto-update produces bad content:
```bash
git checkout -- .opencode/skills/codegen/SKILL.md
```
This is why `.opencode/skills/` should be committed to version control.

---

## 9. Skill File Path Resolution

```python
import os

REPO_ROOT   = os.path.dirname(os.path.abspath(__file__))   # Docker-MAW repo root
SKILLS_ROOT = os.path.join(REPO_ROOT, ".opencode", "skills")

def skill_path(skill_name: str) -> str:
    return os.path.join(SKILLS_ROOT, skill_name, "SKILL.md")
```

Skill names in this repo: `codegen`, `parsl`, `auto_update`.

Note: `skills/Workflow_CrashCourse.md` is a separate domain reference file — the `skill_updater`
does NOT modify it. It is managed manually by the team.

---

## 10. Console Output Convention

```python
console.print("\n[dim cyan][skill_updater] analyzing run history...[/dim cyan]")
# per update:
console.print(f"[dim cyan][skill_updater] updating {update.skill_name}/SKILL.md — {update.reason}[/dim cyan]")
# summary panel:
console.print(Panel(result.summary, title="[bold green]Skill Update Summary[/bold green]", border_style="green"))
```

---

## 11. Adding skill_updater to agent.py

> *** SHARED STATE WARNING ***
> Adding `skill_updater` requires adding `skill_update_summary` to `AgentState` (shared)
> and changing graph wiring (Jacob's domain). Confirm with Jacob before applying.

Changes required:
1. `AgentState` — add `skill_update_summary: str`
2. Schemas — add `SkillFileUpdate` and `SkillUpdaterOutput`
3. Add `SKILL_UPDATER_PROMPT` constant
4. Add `skill_updater()` node function
5. Graph — `graph.add_node("skill_updater", skill_updater)`, reroute `"end"` → `"skill_updater"`, add `skill_updater → END` edge
6. Initial state — add `"skill_update_summary": ""`

---
name: use_cases/molecular_nucleation/planner
description: >
  Extraction rules for molecular nucleation and water crystallization MD papers.
  Covers what parameters to look for, which tools this class of paper uses, the
  HPC-to-local reasoning for LAMMPS and OVITO, and task templates. General enough
  to apply to any nucleation MD paper, not just one specific publication.
---

# Molecular Nucleation — Planner Skill

Extraction and planning rules for the class of papers describing molecular dynamics nucleation simulations — water crystallization, ice nucleation, crystal growth from solution, and related workflows.

---

## When to Use This Skill

Load when planning any molecular nucleation or water crystallization workflow. Provides the parameter extraction patterns, tool-specific reasoning, and task templates for this domain.

---

## First — Check the Goal for Runtime Environment

Before extracting anything from the paper, read the goal. The user specifies where the workflow must run. Load the matching knowledge skill and build the entire plan for that environment.

- Goal says "local", "my machine", "Docker" → load `knowledge/local_docker`, plan for serial execution in a Docker container
- Goal says "LCRC", "Argonne", "cluster", "Singularity" → load `knowledge/lcrc`, plan for Singularity container execution on LCRC
- Goal says "HPC", "cluster", "SLURM" (generic, not LCRC) → load the appropriate HPC knowledge skill, plan for that environment

**Do not default to the paper's HPC environment just because that is what the paper describes. Plan for what the user asked for.**

---

## Data Files — Match Paper to What's Available

The user's prompt includes an **"Available data files"** list — the exact files present in `/app/data/`. You MUST use this list to determine input filenames. Do NOT assume or invent filenames.

- **Input script**: find the file that matches what the paper calls its LAMMPS input script
- **Force field file**: find the file that matches the potential the paper uses
- **Initial configuration**: find the file that matches the paper's initial atom positions

Put the exact matched filenames in your tasks so codegen uses them directly. If a file the paper requires is not in the available list, note it explicitly in `literature_findings` as missing.

---

## What to Extract from the Paper

### Simulation parameters to find and record

- **Temperature (K)** — look for undercooling descriptions, e.g., "180 K" or "50 K below melting point"
- **Timestep (ps or fs)** — e.g., "dt = 0.01 ps"
- **Run length (steps)** — e.g., "9000 steps" or "1 ns"
- **Ensemble** — NPT, NVT, NVE — critical for thermostat/barostat setup
- **Pressure (atm or bar)** — for NPT runs
- **Thermostat and barostat coupling constants** — if stated
- **Force field / potential** — Tersoff AW, TIP4P/Ice, SPC/E, ReaxFF, etc. Note the exact name and citation
- **System size** — number of atoms or box dimensions
- **Random seed** — if stated and reproducibility matters

### Software tools to identify

- **MD engine** — LAMMPS is common but also GROMACS, NAMD, AMBER; extract which one
- **Structure analysis** — OVITO with IdentifyDiamondModifier is common; also Q6 order parameters, RDF, Steinhardt parameters
- **Workflow orchestration** — Parsl, PyCOMPSs, bare Python
- **Visualization** — OVITO rendering, matplotlib, ParaView

### HPC context to note

Note what parallelization and infrastructure the paper used — MPI ranks, number of nodes, SLURM scripts. Record these in `literature_findings` for completeness, but do NOT reproduce them in `stack_decision` or tasks. The `knowledge/local_docker` skill tells you how to translate these to local equivalents.

---

## HPC-to-Local Translation for This Domain

Apply these translations when building `stack_decision` and tasks. The WHY behind each translation is in `knowledge/local_docker`.

| Paper says | Local translation |
|---|---|
| `mpirun -np N lammps -in script.in` | `from lammps import lammps; lmp = lammps(cmdargs=["-screen","none"]); lmp.file(...)` |
| `pip install lammps` | Source build with `BUILD_MPI=off` (pip wheel calls MPI_Init and crashes — see local_docker) |
| `module load lammps` | LAMMPS installed in Dockerfile via source build |
| `module load ovito` | `pip3 install ovito --break-system-packages` |
| Parsl with SlurmProvider | Parsl with LocalProvider + HighThroughputExecutor |
| OVITO headless rendering | Requires osmesa apt packages + headless env vars |

---

## Stack Decision Guidance for This Domain

Use this as a reasoning starting point, not a locked list. The actual `stack_decision` must come from reading the paper and applying the local_docker translations. Different nucleation papers may use different force fields, different analysis tools, or additional packages.

**Typical base:**
- `base_image`: `"ubuntu:24.04"`
- `apt_packages`: system libs for building LAMMPS from source + osmesa for headless OVITO rendering
- `pip_packages`: ovito, parsl>=2024.0.0, numpy, matplotlib, and any paper-specific analysis packages
- `special_installs`: LAMMPS source build if the paper uses LAMMPS (check `knowledge/local_docker` for the MPI trap)
- `env_vars`: headless rendering vars if OVITO or matplotlib visualization is involved

If the paper uses GROMACS instead of LAMMPS, or a different analysis tool instead of OVITO, adjust accordingly. Do not default to the LAMMPS+OVITO stack just because it is familiar.

---

## OVITO-Specific Gotcha — Diamond Structure Type Mapping

If the paper uses OVITO's `IdentifyDiamondModifier` for ice crystal detection, the structure type numbering is:

| Type | Meaning |
|---|---|
| 0 | Other (liquid, amorphous) |
| 1 | Cubic diamond |
| 2 | Cubic diamond (1st neighbor shell) |
| 3 | Cubic diamond (2nd neighbor shell) |
| 4 | Hexagonal diamond (wurtzite ice) |
| 5 | Hexagonal diamond (1st neighbor shell) |
| 6 | Hexagonal diamond (2nd neighbor shell) |

Cubic ice count = types 1 + 2 + 3. Hexagonal ice count = types 4 + 5 + 6.

**This is a known codegen failure point.** Include this explicitly in a task: codegen that counts only type 1 and type 3 misses most crystal atoms and produces near-zero counts that look like the simulation ran but nucleation never happened.

---

## Task Templates

These are examples of the right level of detail. Do not copy them verbatim — derive tasks from what the paper actually describes. Adjust function names, parameters, and steps to match the specific paper's workflow.

**Good level of detail:**
> "Write a Parsl @python_app to run the LAMMPS simulation using its Python API (not subprocess). Copy the force field and initial configuration files into the working directory before running. Copy the input script fresh every run — never skip, the user may have updated it. Change directory into the work folder BEFORE initializing LAMMPS because dump file paths in the input script are relative to CWD. Return the path to the directory where trajectory frames were written."

> "Write a Parsl @python_app to analyze the trajectory with OVITO's IdentifyDiamondModifier. For each frame, count cubic ice as structure types 1+2+3 combined and hexagonal ice as types 4+5+6 combined — not type 1 and type 3 only, which misses most crystal atoms. Write results to CSV with columns: frame, timestep, cubic_count, hexagonal_count."

**Too vague (BAD):**
> "Run LAMMPS and analyze the output."

**Too specific (BAD — you are writing the code):**
> "Call os.makedirs(work_dir, exist_ok=True), then shutil.copy2(os.path.join(data_dir, 'data.init'), work_dir), then os.chdir(work_dir), then lmp = lammps(cmdargs=['-screen','none']); lmp.file(...)"

---

## Key Rules

- Do NOT add tasks for "install LAMMPS" or "build Docker image" — the installer handles that
- The input script (`in.watbox` or equivalent) is user-controlled — codegen must use it as-is, never modify it
- If the paper states a parameter not present in the user's input script, record it in `literature_findings` but do NOT instruct codegen to hardcode it
- Do NOT assume the Tersoff AW force field — extract which force field the paper actually uses

---

## Example Output Structure

```json
{
  "literature_findings": [
    "Water crystallization simulation using LAMMPS with AW Tersoff potential",
    "NPT ensemble at 180 K, 1.0 atm, timestep 0.01 ps, run 9000 steps",
    "Ice structure detection via OVITO IdentifyDiamondModifier",
    "Cubic diamond (types 1-3) and hexagonal diamond (types 4-6) tracked per frame",
    "Paper ran on 8 MPI ranks on HPC cluster — translated to serial local execution"
  ],
  "stack_decision": {
    "base_image": "ubuntu:24.04",
    "apt_packages": ["python3", "python3-pip", "..."],
    "pip_packages": ["ovito", "parsl>=2024.0.0", "numpy", "matplotlib", "Pillow"],
    "special_installs": [{"name": "lammps_source_build_no_mpi", "reason": "..."}],
    "env_vars": {"LIBGL_ALWAYS_SOFTWARE": "1", "PYOPENGL_PLATFORM": "osmesa", "OVITO_GUI_MODE": "0", "LD_LIBRARY_PATH": "/usr/local/lib"},
    "workdir": "/app"
  },
  "tasks": [
    "Define Parsl config with LocalProvider + HighThroughputExecutor ...",
    "Define @python_app run_lammps(...) ...",
    "..."
  ]
}
```

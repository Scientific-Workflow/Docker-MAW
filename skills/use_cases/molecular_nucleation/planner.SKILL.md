---
name: use_cases/molecular_nucleation/planner
description: >
  Molecular nucleation extraction rules for the planner. Covers what parameters to extract
  from LAMMPS water crystallization papers, the correct stack_decision for this project,
  and the exact task structure that codegen needs to produce a working workflow.
---

# Molecular Nucleation — Planner Skill

Extraction and task-writing rules specific to the LAMMPS water crystallization workflow.

---

## When to Use This Skill

Load when planning a molecular nucleation or water crystallization workflow. Provides the exact parameter names, stack constraints, and task templates for this project.

---

## What to Extract from the Paper

### Simulation parameters to find and record
- Temperature (K) — e.g., "180 K undercooling"
- Timestep (ps) — e.g., "dt = 0.01 ps"
- Run length (steps) — e.g., "9000 steps"
- Ensemble — NPT, NVT, NVE
- Pressure (atm or bar) — for NPT runs
- Thermostat / barostat coupling constants
- Force field name — e.g., "Tersoff AW potential", "TIP4P/Ice", "SPC/E"
- System size — number of atoms or box dimensions
- Seed (if stated)

### Software tools to identify
- MD engine: LAMMPS (always present in this project)
- Structure analysis: OVITO with IdentifyDiamondModifier
- Workflow orchestration: Parsl
- Any post-processing tools

---

## Stack Decision — Constrained to Dockerfile

The sandbox Dockerfile provides EXACTLY these packages. Use only these:

| Package | Version constraint |
|---|---|
| lammps | (source-built, no version flag needed) |
| ovito | (latest installed) |
| parsl | `parsl>=2024.0.0` |
| numpy | (latest installed) |
| matplotlib | (latest installed) |
| Pillow | (required for GIF generation) |

**Do NOT add:** scipy, ase, mdanalysis, mpi4py, h5py, or any other package. They are not in the container and will cause `ModuleNotFoundError`.

---

## Task Templates

The following is an example of how you would set up your task list. Do not copy this structure exactly. If you need more tasks to achieve the goal level of detail, please do so.
If you need less tasks becuase you have already reached the goal level of detail, that is allowed as well.

Aim for 10–12 tasks. Each task describes a purpose and its critical constraints — not a specific implementation. Leave structure, variable names, and code style to codegen.

---

## Few-Shot Examples — Target Level of Detail

### Too vague (BAD — codegen will guess and get the constraints wrong)
> "Run LAMMPS and analyze the output."
> "Set up the simulation and write results to a file."

### Too specific (BAD — you're writing the code, not describing a task)
> "Inside run_lammps, call os.makedirs(work_dir, exist_ok=True), then shutil.copy2(os.path.join(data_dir, 'data.init'), work_dir), then os.chdir(work_dir), then from lammps import lammps; lmp = lammps(cmdargs=['-screen','none']); lmp.file(...); lmp.close()"

### Just right (GOOD — states the purpose and only the constraints that actually matter)
> "Write a Parsl app to run the LAMMPS simulation. Copy the required data files and input script into the working directory before running — always re-copy the script fresh in case the user updated it. Change directory into the run folder BEFORE starting LAMMPS because dump paths are relative to CWD. Use the LAMMPS Python API, not subprocess. Never modify the input script."

> "Write a Parsl app to analyze the trajectory with OVITO's IdentifyDiamondModifier. Count cubic ice as structure types 1, 2, and 3 combined, and hexagonal ice as types 4, 5, and 6 combined — not 1-2 and 3-4, which misclassifies type 3. Output a CSV with frame, timestep, and both counts per frame."

> "Write a Parsl app that renders atom positions per frame as a 3D scatter plot. Liquid atoms in cyan, cubic crystal atoms in blue, hexagonal crystal atoms in red. Atom size must be large enough to see the structure (s=25 minimum, alpha >= 0.6). Save individual frames and assemble them into an animated GIF."

---

```
1. "Set up a single-node Parsl workflow executor — local machine only, no HPC, no MPI. Load the config before defining any apps."

2. "Write a Parsl app to run the LAMMPS simulation. It should copy the force field and data files into the working directory, then copy the input script fresh every run (never skip — the user may have updated it). Change the working directory into the run folder BEFORE starting LAMMPS, because the dump paths in the script are relative to CWD. Run LAMMPS using its Python API (not subprocess), do not modify the input script in any way. Return the path to where trajectory frames were written."

3. "Write a Parsl app to analyze the trajectory with OVITO. Load the dump files using ovito.io.import_file and apply IdentifyDiamondModifier. For each frame, count cubic ice as structure types 1, 2, and 3 combined — and hexagonal ice as types 4, 5, and 6 combined. Do not use only types 1-2 and 3-4; that misclassifies type 3 which is cubic, not hexagonal. Write results to a CSV with columns: frame, timestep, cubic_diamond_count, hexagonal_diamond_count."

4. "Write a Parsl app to render each trajectory frame as a 3D atom visualization. Color liquid/unstructured atoms cyan (#00BFFF), cubic diamond atoms blue (#0000FF), and hexagonal diamond atoms red (#FF2200). Use a minimum atom size of s=25 and alpha of at least 0.6 for all types so atoms are visible. Save individual frame images to a renders subfolder."

5. "After rendering individual frame images, assemble them into an animated GIF using Pillow (PIL.Image). Save the GIF alongside the frame renders."

6. "Write a Parsl app to generate a nucleation time series plot from the CSV — show cubic and hexagonal ice atom counts over timestep. Use the same blue/red color scheme as the visualization. Save as a PNG."

7. "Write a main() function that accepts two command-line arguments: a data directory and a work directory. Derive the input script path from the data directory — do not accept it as a separate argument. Chain the simulation, analysis, visualization, and time series steps in order, waiting for each to finish before starting the next. Clean up Parsl at the end."

8. "Write a bash launcher script that resolves paths relative to the script file itself (never from the current working directory), accepts an optional Docker image tag, mounts the repo root into the container, and calls the workflow with the data and work directory arguments only."
```

---

## Key Rules

- Do NOT add tasks for "install LAMMPS" or "build Docker image" — the sandbox is pre-built
- Do NOT add tasks involving MPI, mpirun, or any multi-node setup
- If the paper uses a parameter not mentioned in the current in.watbox, note it in literature_findings but do NOT instruct codegen to hardcode it — the in.watbox file controls the simulation
- The input script (`in.watbox`) is user-controlled; codegen must use it as-is

---

## Example Output

```json
{
  "literature_findings": [
    "Water crystallization simulation using LAMMPS with AW Tersoff potential",
    "NPT ensemble at 180 K, 1.0 atm",
    "Timestep: 0.01 ps, run length: 9000 steps",
    "Ice structure detection via OVITO IdentifyDiamondModifier",
    "Cubic diamond (types 1-3) and hexagonal diamond (types 4-6) tracked per frame"
  ],
  "stack_decision": ["lammps", "ovito", "parsl>=2024.0.0", "numpy", "matplotlib", "Pillow"],
  "tasks": [
    "Define @python_app run_lammps(...) ...",
    "Define @python_app analyze_with_ovito(...) ...",
    "Define @python_app render_frames(...) ...",
    "Define main() with --data-dir and --work-dir argparse only ..."
  ]
}
```

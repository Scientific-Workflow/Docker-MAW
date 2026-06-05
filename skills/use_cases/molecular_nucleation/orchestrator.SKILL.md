---
name: use_cases/molecular_nucleation/orchestrator
description: >
  Molecular nucleation routing rules for the orchestrator. Covers the pre-built Docker image
  flow (no installer step), LAMMPS executor error pattern recognition, and Dockerfile approval
  behavior for the source-built LAMMPS image.
---

# Molecular Nucleation — Orchestrator Skill

Routing rules specific to the water crystallization / LAMMPS workflow.

---

## When to Use This Skill

Load when orchestrating a molecular nucleation workflow. Overrides generic routing heuristics with LAMMPS-specific error pattern recognition.

---

## Flow for This Project

```
planner → codegen → executor → end
```

**The sandbox Docker image is pre-built.** Do NOT route to the installer under any circumstances. The `maw-sandbox:latest` image is always available — routing to installer wastes time and may trigger an unnecessary 20-minute rebuild.

---

## Executor Error Pattern Recognition

When executor returns a non-zero exit code, use these patterns to decide where to route:

| Error pattern in stderr | Route to | Feedback |
|---|---|---|
| `unrecognized arguments` | codegen | "workflow.py uses wrong argparse interface. Must accept only --data-dir and --work-dir. Remove any other arguments." |
| `WorkerLost` + `MPI` / `ORTE` | codegen | "LAMMPS MPI init failure in Parsl worker. Do not use pip lammps wheel — use `from lammps import lammps` from the source-built install. Ensure LD_LIBRARY_PATH=/usr/local/lib is set before import." |
| `ModuleNotFoundError: No module named 'lammps'` | codegen | "LAMMPS not found. Use `from lammps import lammps` — the source build is at /usr/local/lib. Do not pip install lammps." |
| `ModuleNotFoundError: No module named 'PIL'` | codegen | "Pillow is installed as 'Pillow' not 'PIL'. Import with `from PIL import Image`." |
| `frames/step.*.lammpstrj` not found / no frames | codegen | "LAMMPS did not produce dump files. Ensure os.chdir(work_dir) is called BEFORE lammps() and that work_dir/frames/ exists." |
| `results.csv` missing after exit 0 | codegen | "analyze_with_ovito did not write results.csv. Check the output_csv path and that pipeline.compute() loop runs." |
| All other failures | codegen | Full stderr content |

---

## Dockerfile Approval

If the installer ever runs (it normally won't), the Dockerfile to approve must have:
- `FROM ubuntu:24.04`
- LAMMPS built from source with `BUILD_MPI=off`
- `ENV LD_LIBRARY_PATH=/usr/local/lib` (no `$LD_LIBRARY_PATH` self-reference)
- `pip3 install ovito parsl numpy matplotlib Pillow` — Pillow is required
- NO libopenmpi-dev, openmpi-bin, or any MPI packages
- NO `pip install lammps` (pip wheel)

---

## Notes

- After a successful run (exit 0, results.csv present), route to "end"
- Codegen revision trigger: any executor error that is not a missing pip package
- Never route back to planner unless the task description itself was wrong (rare)

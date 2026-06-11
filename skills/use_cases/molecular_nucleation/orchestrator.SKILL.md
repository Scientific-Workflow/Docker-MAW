---
name: use_cases/molecular_nucleation/orchestrator
description: >
  Molecular nucleation routing rules for the orchestrator. Covers the installer flow,
  LAMMPS executor error pattern recognition, and conda env spec approval criteria
  derived from stack_decision rather than hardcoded content.
---

# Molecular Nucleation — Orchestrator Skill

Routing rules specific to the water crystallization / LAMMPS workflow.

---

## Flow for This Project

```
planner → installer → codegen → executor → end
```

The installer always runs after the planner. The planner produces a `stack_decision` that the installer uses to generate and build the `maw_sandbox` conda environment. Do not skip the installer.

---

## After Planner — Extra Checks for This Domain

In addition to the base orchestrator checks, verify:

- `stack_decision.special_installs` includes a LAMMPS source build entry — the pip wheel calls `MPI_Init` on import and crashes without an MPI daemon. If it only lists `lammps` in `pip_packages`, send back: `"LAMMPS must be source-built — the pip wheel calls MPI_Init on import and crashes. Add lammps_source_build_no_mpi to special_installs and remove lammps from pip_packages"`
- `stack_decision.env_vars` includes the three headless rendering vars (`LIBGL_ALWAYS_SOFTWARE`, `PYOPENGL_PLATFORM`, `OVITO_GUI_MODE`) — OVITO requires these for headless execution on any machine without a display
- `stack_decision.env_vars` includes `LD_LIBRARY_PATH` set to `$CONDA_PREFIX/lib` — required for the source-built LAMMPS to be importable
- Tasks do not contain `mpirun`, `mpiexec`, or `subprocess` calls to LAMMPS — LAMMPS must be invoked via its Python API

---

## Conda Env Spec Approval — Validate Against stack_decision

Do not approve or reject the env spec against a hardcoded template. Validate against the `stack_decision` the planner produced:

- Every package in `stack_decision.apt_packages` must have a corresponding conda-forge translation in `environment.yml` (installer handles the translation; verify nothing was dropped)
- Every package in `stack_decision.pip_packages` must appear in the `pip:` section of `environment.yml`
- Every entry in `stack_decision.special_installs` must have corresponding build steps in `install.sh`
- Every key in `stack_decision.env_vars` must NOT appear in `environment.yml` — they are passed at runtime by the executor, not baked into the env

---

## Executor Error Pattern Recognition

When executor returns a non-zero exit code, use these patterns to route:

| Error pattern in stderr | Route to | Feedback |
|---|---|---|
| `unrecognized arguments` | codegen | "workflow.py uses wrong argparse interface. Must accept only --data-dir and --work-dir. Remove any other arguments." |
| `WorkerLost` + `MPI` / `ORTE` | codegen | "LAMMPS MPI init failure in Parsl worker. Use `from lammps import lammps` from the source-built install. Ensure LD_LIBRARY_PATH is set inside the @python_app." |
| `ModuleNotFoundError: No module named 'lammps'` | codegen | "LAMMPS not importable. Use `from lammps import lammps` — source build installs to $CONDA_PREFIX/lib. Ensure LD_LIBRARY_PATH is set inside the @python_app before the import." |
| `ModuleNotFoundError: No module named 'X'` (any other package) | planner | "Package X is missing from stack_decision. Update stack_decision.pip_packages to include X, then installer will rebuild the conda env." |
| `ModuleNotFoundError: No module named 'PIL'` | codegen | "Import Pillow as `from PIL import Image` — the package name is Pillow but the import is PIL." |
| `frames/step.*.lammpstrj` not found / no frames | codegen | "LAMMPS did not produce dump files. Ensure os.chdir(work_dir) is called BEFORE lammps() — dump paths are relative to CWD. Also verify work_dir/frames/ is created before running." |
| `results.csv` missing after exit 0 | codegen | "OVITO analysis did not write results.csv. Check the output path and that pipeline.compute() loop runs for all frames." |
| All other failures | codegen | Include full stderr content in feedback |

---

## Notes

- After successful run (exit 0 + results.csv present + renders/ present): route to `end`
- Never route back to planner unless executor reveals a missing package or tasks were fundamentally wrong
- The installer's hash-based skip means re-routing through installer after a codegen fix does NOT trigger a rebuild unless stack_decision changed

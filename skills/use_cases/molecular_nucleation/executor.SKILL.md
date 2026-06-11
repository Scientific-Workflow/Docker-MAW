---
name: use_cases/molecular_nucleation/executor
description: >
  Executor rules for the molecular nucleation project. Covers expected output files,
  exit code interpretation, and stale data warnings. Workflow runs via conda run
  with real host paths.
---

# Molecular Nucleation — Executor Skill

Execution and output expectations for the LAMMPS water crystallization workflow.

---

## Expected Successful Output

After exit code 0, the following must exist in the per-run work directory (`work/run_YYYYMMDD_HHMMSS/`):

```
work/run_YYYYMMDD_HHMMSS/
├── frames/
│   └── step.*.lammpstrj      ← trajectory dumps (one per Nrestart=100 steps)
├── results.csv               ← nucleation analysis (frame, timestep, cubic_count, hexag_count)
└── renders/
    ├── frame_*.png           ← per-frame atom renders
    └── animation.gif         ← GIF animation of crystallization
```

If `results.csv` is missing after exit 0, the analysis step failed silently — treat as failure.

---

## Conda Run Command

The executor calls:

```bash
conda run -n maw_sandbox --no-capture-output \
  python3 /abs/path/to/builds/workflow.py \
  --data-dir /abs/path/to/work/run_YYYYMMDD_HHMMSS/data \
  --work-dir /abs/path/to/work/run_YYYYMMDD_HHMMSS
```

Pass ONLY `--data-dir` and `--work-dir`. No `--input-script`.
All paths are real host filesystem paths.

env_vars from `stack_decision` (e.g. `LIBGL_ALWAYS_SOFTWARE=1`) are passed via `subprocess env=` dict, not as `-e` flags.

---

## Stale Data Warning

Each run uses a new timestamped directory — `work/run_YYYYMMDD_HHMMSS/` — so stale data from previous runs does not interfere. Do not reuse old run directories.

---

## Exit Code Interpretation

| Exit code | Meaning |
|---|---|
| 0 + results.csv exists | Full success |
| 0 + results.csv missing | Partial failure — analysis step crashed silently |
| 1 + `unrecognized arguments` | workflow.py argparse mismatch — route to codegen |
| 1 + `WorkerLost` + MPI | LAMMPS MPI init crash — route to codegen |
| 1 + `ModuleNotFoundError` | Missing package — check if pip-installable, update stack_decision |
| 1 + `conda env ... not found` | maw_sandbox env was not created — route to installer |
| 1 + `No such file or directory` (workflow.py) | codegen did not run or wrote to wrong path — route to codegen |

---
name: use_cases/molecular_nucleation/executor
description: >
  Executor rules for the molecular nucleation project. Covers expected output files,
  exit code interpretation, HOST_REPO_PATH requirements on Windows, and stale data warnings.
---

# Molecular Nucleation — Executor Skill

Execution and output expectations for the LAMMPS water crystallization workflow.

---

## Expected Successful Output

After exit code 0, the following must exist:

```
/app/work/run0/
├── frames/
│   └── step.*.lammpstrj      ← trajectory dumps (one per Nrestart=100 steps)
├── results.csv               ← nucleation analysis (frame, timestep, cubic_count, hexag_count)
└── renders/
    ├── frame_*.png           ← per-frame atom renders
    └── animation.gif         ← GIF animation of crystallization
```

If `results.csv` is missing after exit 0, the analysis step failed silently — treat as failure.

---

## Docker Run Command

```bash
docker run --rm \
  -v "$HOST_REPO_PATH":/app \
  -w /app/builds \
  -e LIBGL_ALWAYS_SOFTWARE=1 \
  -e PYOPENGL_PLATFORM=osmesa \
  -e OVITO_GUI_MODE=0 \
  maw-sandbox:latest \
  python3 /app/builds/workflow.py \
  --data-dir /app/data \
  --work-dir /app/work/run0
```

Pass ONLY `--data-dir` and `--work-dir`. No `--input-script`.

---

## HOST_REPO_PATH on Windows

`HOST_REPO_PATH` must be the real Windows host path, not the container path `/app`. Set before running:

```powershell
$env:HOST_REPO_PATH = (Get-Location).Path
```

If not set, the Docker volume mount will use the wrong path and `/app/data/` will be empty inside the container.

---

## Stale Data Warning

If `work/run0/frames/` already contains `.lammpstrj` files from a previous run, OVITO will read those instead of running a fresh simulation. The LAMMPS step may still complete (it overwrites the frames), but if it fails early, old frames remain and OVITO processes stale data.

**Clean between runs:**
```powershell
Remove-Item -Recurse -Force work
```

---

## Exit Code Interpretation

| Exit code | Meaning |
|---|---|
| 0 + results.csv exists | Full success |
| 0 + results.csv missing | Partial failure — analysis step crashed silently |
| 1 + `unrecognized arguments` | workflow.py argparse mismatch — route to codegen |
| 1 + `WorkerLost` + MPI | LAMMPS MPI init crash — route to codegen |
| 1 + `ModuleNotFoundError` | Missing package — check if pip-installable |
| 125 + `invalid reference format` | Docker image tag is empty string — check image_tag state field |
| 125 + `docker: invalid reference` | Same as above |

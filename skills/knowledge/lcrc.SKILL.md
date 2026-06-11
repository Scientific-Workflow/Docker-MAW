---
name: knowledge/lcrc
description: >
  Runtime environment knowledge for running MAW on the Argonne LCRC cluster using
  conda environments. Covers MAW agent setup, workflow execution model, LCRC storage
  paths, and what differs from local machine execution. Load when goal mentions
  LCRC, Argonne, cluster, or HPC.
---

# LCRC — Knowledge Skill

## When to Use This Skill

Load when the goal mentions LCRC, Argonne, cluster, or HPC. This skill supplements `knowledge/local_machine` — the same conda execution model applies, with LCRC-specific setup steps and storage path rules layered on top.

---

## How MAW Runs on LCRC

The MAW agent runs as a Python process in the `maw` conda environment:

```bash
module load anaconda3
conda activate maw
python agent.py
```

Generated workflows run in the `maw_sandbox` conda environment created by the installer:

```bash
conda run -n maw_sandbox --no-capture-output \
  python3 builds/workflow.py \
  --data-dir /abs/path/to/data \
  --work-dir /abs/path/to/work/run_id
```

The conda environments are built and run directly on the LCRC node.

---

## What Stays the Same as Local Machine

Everything in `knowledge/local_machine` applies on LCRC:
- HPC-to-local translation (MPI → serial Python API, SLURM → Parsl LocalProvider)
- Headless rendering (no display on compute nodes — osmesa required, same env vars)
- LAMMPS MPI trap (source build with BUILD_MPI=off)
- LD_LIBRARY_PATH for source-built libraries (`$CONDA_PREFIX/lib`)
- stack_decision format (conda-forge packages, real host paths)

---

## What is Different on LCRC

### Module System

Conda is available via the module system. Load it before activating the MAW env:

```bash
module load anaconda3
conda activate maw
python agent.py
```

### Storage Paths

Use the correct filesystem for each purpose:

| Path | Use for |
|---|---|
| `/lcrc/project/<project>/` | Project repo, conda envs, results — persistent, backed up |
| `/lcrc/globalscratch/<username>/` | Large intermediate output — shared, may be cleared during maintenance |
| `/scratch` | 15 GB node-local scratch — cleared after job ends, DO NOT store conda envs here |

Store the project repo and `maw_sandbox` conda env under `/lcrc/project/`.

### Compute Nodes

If running on a compute node (not login node), include `module load anaconda3` in your job script before `conda activate maw`. Build the conda env on a login node if `install.sh` downloads source tarballs — login nodes have internet access, compute nodes may not.

### No Display

Compute nodes have no display server. Headless rendering (osmesa, env vars) is required — same as local machine. Already covered in `stack_decision.env_vars`.

---

## stack_decision for LCRC

Same as `knowledge/local_machine`. Linux packages apply (`gxx_linux-64`, `gcc`, `make` all work — LCRC nodes are Linux x86_64).

---

## HPC-to-Local Translation

All translations from `knowledge/local_machine` apply:
- `mpirun -np N lammps` → LAMMPS Python API, serial
- `module load X` → conda package or `install.sh` source build
- `SlurmProvider` → `LocalProvider`
- `/scratch/user/data/` → `--data-dir` (real host path)
- `#SBATCH --nodes=4` → ignored, single node execution

---

## LCRC-Specific Notes

- Available clusters: **Bebop** (CPU, Intel Xeon), **Swing** (GPU, A100), **Improv**
- `conda env create` for a LAMMPS build takes ~20 minutes — the hash-based skip in the installer prevents unnecessary rebuilds on subsequent runs
- **Project storage** (persistent, backed up): `/lcrc/project/<project_name>/` — store the project repo here
- **Global scratch** (temporary): `/lcrc/globalscratch/<username>/` — large intermediate files; may be cleared during maintenance
- **Node-local scratch**: `/scratch` — 15 GB, local to each compute node, cleared after job ends

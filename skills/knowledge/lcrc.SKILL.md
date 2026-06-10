---
name: knowledge/lcrc
description: >
  Runtime environment knowledge for running MAW-generated workflows on the Argonne
  LCRC cluster using Singularity containers. Covers agent setup (bare conda env),
  Singularity command translations from Docker, volume mounts, env vars, and what
  stays the same vs what changes from the local_docker skill.
---

# LCRC — Knowledge Skill

## When to Use This Skill

Load when the goal mentions LCRC, cluster, HPC, Argonne, or Singularity. This skill replaces `knowledge/local_docker` for cluster execution — do not load both.

---

## How MAW Runs on LCRC

On LCRC the MAW agent runs **bare** — no Docker container wrapping the agent itself. The agent is a Python process in a conda environment:

```bash
module load anaconda3
conda activate maw
python agent.py
```

`HOST_REPO_PATH` must be set as a shell environment variable pointing to the project directory on LCRC before running the agent:

```bash
export HOST_REPO_PATH=/path/to/Docker-MAW-main
python agent.py
```

The agent generates code and builds a sandbox environment exactly as on local Docker. The difference is in HOW that sandbox is built and run.

---

## Container Format: Singularity .sif

On LCRC the sandbox is a Singularity Image File (`.sif`), not a Docker image. The workflow is:

1. **Local machine:** agent builds Docker image → exports `builds/maw-sandbox.tar`
2. **Transfer:** `scp builds/maw-sandbox.tar username@lcrc.anl.gov:/path/to/project/builds/`
3. **On LCRC:** convert tar to .sif once:

```bash
module load singularity
singularity build builds/maw-sandbox.sif docker-archive://builds/maw-sandbox.tar
```

The `.sif` is then used for all workflow executions.

---

## Command Translation — Docker vs Singularity

| Docker | Singularity equivalent |
|---|---|
| `docker run --rm` | `singularity exec` (no --rm needed, containers don't persist) |
| `-v /host/path:/container/path` | `--bind /host/path:/container/path` |
| `-e KEY=VALUE` | `--env KEY=VALUE` |
| `-w /app/builds` | `--pwd /app/builds` |
| `docker build -t tag -f Dockerfile .` | `singularity build image.sif docker-archive://image.tar` |
| `docker images -q tag` | `ls builds/maw-sandbox.sif` |

**Full executor command example:**

Docker:
```bash
docker run --rm \
  -v $HOST_REPO_PATH:/app \
  -v $HOST_REPO_PATH/work/run_id/data:/app/data \
  -w /app/builds \
  -e LIBGL_ALWAYS_SOFTWARE=1 \
  maw-sandbox:latest \
  python3 /app/builds/workflow.py --data-dir /app/data --work-dir /app/work/run_id
```

Singularity equivalent:
```bash
module load singularity
singularity exec \
  --bind $HOST_REPO_PATH:/app \
  --bind $HOST_REPO_PATH/work/run_id/data:/app/data \
  --pwd /app/builds \
  --env LIBGL_ALWAYS_SOFTWARE=1 \
  builds/maw-sandbox.sif \
  python3 /app/builds/workflow.py --data-dir /app/data --work-dir /app/work/run_id
```

---

## Key Differences from Docker

### Runs as the calling user, not root

Singularity containers run as the user who invokes them — not root. This means:
- Output files in `work/` are owned by you (good for HPC)
- No permission errors on bind-mounted directories
- Do NOT write code that assumes root inside the container

### No Docker daemon required

No Docker socket, no `docker.sock` mount, no `HOST_DOCKER_SOCK`. Singularity builds and runs directly without a daemon.

### `module load singularity` before any singularity command

Singularity is not in PATH by default on LCRC. Always load the module first:
```bash
module load singularity
```

### Bind mounts are read-write by default

Unlike Docker where mounts default to read-write, Singularity also defaults to read-write for `--bind`. The `/app/work/` output directory will be writable.

---

## What Stays the Same

Everything that runs **inside** the container is identical to the Docker case. The container filesystem, installed packages, and runtime behavior do not change.

| Component | Same as Docker |
|---|---|
| Base image | `ubuntu:24.04` — built locally, converted to .sif |
| All apt/pip packages | Identical — built into the image during Docker build |
| Parsl `LocalProvider` + `HighThroughputExecutor` | Works identically inside the container |
| `run_dir='/tmp/parsl_runinfo'` | Still mandatory — same bind-mount permissions reason |
| `ENV LD_LIBRARY_PATH=/usr/local/lib` | Still needed for source-built libraries |
| Headless rendering (osmesa, env vars) | Still required — no display on cluster compute nodes either |
| LAMMPS source build, `BUILD_MPI=off` | Same — no MPI runtime inside the container |
| `/app/data`, `/app/work` paths | Same — set by `--bind` mounts |

---

## stack_decision for LCRC

The `stack_decision` is identical to the Docker case — the container is built with Docker locally and converted, so the same base image and packages apply. The only difference is in how the executor runs it.

Do NOT change `base_image` to a Singularity-specific image. Keep `ubuntu:24.04`.

---

## HPC-to-Local Translation

All the same translations from `knowledge/local_docker` apply:
- `mpirun -np N lammps` → LAMMPS Python API, serial
- `module load X` → installed in the container image
- `SlurmProvider` → `LocalProvider`
- `/scratch/user/data/` → `/app/data/` via `--bind`
- `#SBATCH --nodes=4` → ignored, single node execution

The container abstracts away the cluster environment just as Docker does. The workflow code does not know or care whether it is running in Docker or Singularity.

---

## Known LCRC-Specific Notes

- Singularity module name: `singularity` (confirm with `module avail singularity` on login node)
- **Project storage** (persistent, backed up): `/lcrc/project/<project_name>/` — store the project repo and `.sif` files here
- **Global scratch** (shared, temporary): `/lcrc/globalscratch/<username>/` — use for large intermediate output; may be cleared during maintenance
- **Node-local scratch**: `/scratch` — 15 GB, local to each compute node, cleared after job ends. Do NOT store .sif here
- `HOST_REPO_PATH` must be exported in the shell before running the agent, e.g. `export HOST_REPO_PATH=/lcrc/project/<project>/Docker-MAW-main`
- The `.sif` file can be 3–8 GB for a LAMMPS image — store it under `/lcrc/project/`, not `/scratch`
- Build the `.sif` on a login node — login nodes have internet access; compute nodes may not
- Available clusters: **Bebop** (CPU, Intel Xeon), **Swing** (GPU, A100), **Improv**

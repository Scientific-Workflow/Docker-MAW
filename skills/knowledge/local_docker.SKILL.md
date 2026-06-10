---
name: knowledge/local_docker
description: >
  Mental model and translation guide for running HPC-described scientific workflows
  inside a Docker container — whether on a local machine or an HPC node. Explains
  WHY the gap exists and HOW to reason across it for any paper.
---

# When to Use This Skill

Load this skill whenever the workflow runs inside a **Docker container** — regardless of where that container is launched. This includes:
- A local developer machine or laptop
- A workstation
- A Docker container submitted to an HPC cluster (e.g., via Singularity, Shifter, or Docker on an HPC node)

The key is the container, not the machine. Inside Docker, the workflow has no MPI fabric, no job scheduler, and no shared cluster filesystem — whether the container is on a laptop or an Argonne node.

Do NOT load this skill if the goal says to run natively on HPC without a container (bare SLURM, real MPI parallelism across nodes).

# Docker Container Execution — Knowledge Skill

## The Core Gap

Scientific papers describe workflows built for HPC clusters: hundreds of CPU cores, distributed memory across nodes, high-speed network fabric (InfiniBand, Ethernet), job schedulers (SLURM, PBS), and shared parallel filesystems. MAW runs these workflows inside a single Docker container on a developer machine.

Understanding WHY these environments differ — not just WHAT to avoid — lets you reason correctly about papers you have never seen before.

---

## Why HPC Software Does Not Run Directly in Docker

### MPI (Message Passing Interface)

HPC papers run parallel jobs with commands like `mpirun -np 128 lammps -in script.in`. MPI is a communication library that coordinates memory and messaging between processes running on different physical nodes connected by a network fabric. Inside a Docker container there is one node and one process space — there is no fabric, no remote nodes, and nothing for MPI to connect to.

More critically: many scientific tools call `MPI_Init` on import even when you never explicitly use MPI parallelism. The ORTE daemon (`orted`) then searches PATH for other nodes, fails to find them, and crashes — before any science runs at all.

**How to reason through it:** Find the serial execution path. Most HPC tools support single-process serial mode — it is just not the default invocation shown in a paper. The paper shows `mpirun` because that is how the authors ran it on their cluster. The same tool can almost always run serially with a different invocation or build flag.

### Job Schedulers (SLURM, PBS, LSF)

HPC papers submit work to job queues: `sbatch job.sh`, `qsub`, PBS array jobs. These schedulers allocate nodes across a cluster and manage execution across many machines. Inside Docker there is no scheduler, no queue, and no node allocation.

**How to reason through it:** Run tasks directly. Use Parsl's `LocalProvider` with `HighThroughputExecutor` to get the same task-graph execution model (dependencies, parallelism, futures) without a scheduler. A Parsl workflow written for SLURM is structurally identical to one written for local — only the provider config changes.

### Module System (`module load`)

HPC clusters use environment modules to manage software versions: `module load lammps/2023`, `module load python/3.11`. There is no module system in Docker.

**How to reason through it:** Every `module load` in a paper is a signal that something needs to be installed in the Dockerfile. The module name tells you what package to find and install via apt or pip or source build.

### Shared Filesystems and Input Paths

HPC jobs read from `/scratch/`, `/project/`, or `/gpfs/` shared network filesystems visible to all nodes. These paths do not exist in Docker.

**How to reason through it:** All input data must be bind-mounted from the host at run time. In MAW the convention is to mount the repo root at `/app`, with input data at `/app/data/` and output at `/app/work/`. Never hardcode HPC filesystem paths — map them to these local equivalents.

---

## Translation Table — Paper Says vs. Local Equivalent

| Paper says | What it is actually doing | Local equivalent |
|---|---|---|
| `mpirun -np 8 lammps -in script.in` | Running LAMMPS, MPI is optional | `from lammps import lammps; lmp = lammps(cmdargs=["-screen","none"]); lmp.file(...)` |
| `module load lammps/2023` | Making LAMMPS available in PATH | Install LAMMPS in the Dockerfile (pip or source) |
| `#SBATCH --nodes=4` | Requesting 4 compute nodes | Ignore — run on one node |
| `parsl.load(config)` with `SlurmProvider` | Submitting tasks to SLURM | Replace provider with `LocalProvider` + `HighThroughputExecutor` |
| `from mpi4py import MPI` | Distributing work across processes | Serialize — run tasks sequentially or with Parsl local workers |
| `module load ovito` | Making OVITO available | `pip3 install ovito --break-system-packages` in Dockerfile |
| `/scratch/user/data/` | HPC shared filesystem path | `/app/data/` inside the container (bind-mounted from host) |
| `mpiexec -np 1 ...` | Running a single MPI process | Run the tool directly without mpiexec |

The goal is not to replicate the parallelism — it is to replicate the scientific computation with the same tools running in serial or thread-parallel mode inside one container.

---

## Known Runtime Gotchas for Docker Containers

These are non-obvious facts about running scientific software in a headless Docker container. You cannot derive these from reading a paper — they are facts about the execution environment that must be encoded explicitly.

### Headless Rendering (OVITO, matplotlib, VTK, ParaView, any OpenGL tool)

Visualization tools expect a display server (X11, Wayland). Docker containers have none by default. Running OVITO, PyOpenGL, or any OpenGL-dependent tool without a display fails with errors like `Cannot connect to display`, `libEGL warning`, or `osmesa: failed to initialize`.

**Fix:** Use OSMesa (a software rasterizer). OSMesa provides the OpenGL interface without a physical display by rendering entirely in CPU memory.

Required apt packages: `libosmesa6`, `libgl1`, `libegl1`, `libopengl0`, `libglib2.0-0`, `libxkbcommon0`, `libdbus-1-3`

Required env vars at runtime:
- `LIBGL_ALWAYS_SOFTWARE=1` — forces software rendering
- `PYOPENGL_PLATFORM=osmesa` — tells PyOpenGL to use OSMesa backend
- `OVITO_GUI_MODE=0` — tells OVITO to disable GUI entirely

Required in any worker function that uses matplotlib: `matplotlib.use("Agg")` must be called inside the function body before any import of `matplotlib.pyplot`.

### The LAMMPS MPI Trap (pip wheel vs source build)

The `pip install lammps` wheel is compiled against OpenMPI and calls `MPI_Init` on import regardless of whether you use MPI. In a container without OpenMPI's `orted` daemon in PATH, this produces `ORTE_ERROR_LOG` / `orte_init failure` before any simulation starts. The error appears in Parsl worker stderr as `WorkerLost`.

There are two fixes:
1. **Source build (preferred):** Build LAMMPS from source with `-DBUILD_MPI=off`. This produces a fully serial Python binding with zero MPI dependency. Takes ~20 minutes to build but runs reliably anywhere.
2. **Runtime shim:** Install `libopenmpi3` + `openmpi-bin` and add `/usr/lib/openmpi/bin` to PATH so `orted` can be found in singleton mode. Faster to build but more fragile.

The source build is preferred when the goal is maximum reproducibility. Use the runtime shim only when build time is a hard constraint.

### pip and Ubuntu 24.04 (PEP 668)

Ubuntu 24.04 marks its Python installation as externally managed. Running `pip3 install <package>` without `--break-system-packages` fails with `error: externally-managed-environment`. Every `pip3 install` line in the Dockerfile must include `--break-system-packages`.

### LD_LIBRARY_PATH for Source-Built Libraries

When building a tool from source and installing to `/usr/local`, the dynamic linker must be able to find the `.so` files. Set `ENV LD_LIBRARY_PATH=/usr/local/lib` in the Dockerfile.

**Critical:** Do NOT write `ENV LD_LIBRARY_PATH=/usr/local/lib:$LD_LIBRARY_PATH` — the `$LD_LIBRARY_PATH` self-reference evaluates to an empty string at build time and produces a leading colon in the path, which causes subtle linker failures.

### GPU Availability

Docker containers on a standard developer machine have no GPU unless explicitly configured with `--gpus all` and NVIDIA Container Toolkit. If a paper uses GPU acceleration (CUDA, cuDNN), assume it is not available unless otherwise specified. Plan for CPU-only execution.

---

## What a Complete `stack_decision` Looks Like

The planner must output a `stack_decision` detailed enough that the installer can generate a Dockerfile with no additional reasoning. Every field must be filled. If a field is vague or missing, the installer will guess — and guesses cause failed builds and wasted rebuild time (~20 minutes per rebuild for complex images).

```json
{
  "base_image": "ubuntu:24.04",
  "apt_packages": [
    "python3", "python3-pip", "python3-dev",
    "build-essential", "cmake", "wget", "git",
    "libfftw3-dev", "libpng-dev", "libjpeg-dev",
    "libosmesa6", "libgl1", "libegl1", "libopengl0",
    "libglib2.0-0", "libxkbcommon0", "libdbus-1-3"
  ],
  "pip_packages": ["ovito", "parsl>=2024.0.0", "numpy", "matplotlib", "Pillow"],
  "special_installs": [
    {
      "name": "lammps_source_build_no_mpi",
      "reason": "pip lammps wheel calls MPI_Init on import and crashes in Docker without a running MPI daemon; source build with BUILD_MPI=off has zero MPI dependency and runs reliably"
    }
  ],
  "env_vars": {
    "LIBGL_ALWAYS_SOFTWARE": "1",
    "PYOPENGL_PLATFORM": "osmesa",
    "OVITO_GUI_MODE": "0",
    "LD_LIBRARY_PATH": "/usr/local/lib"
  },
  "workdir": "/app"
}
```

### Rules for filling each field

**`base_image`:** Choose based on what the tools require. Ubuntu 24.04 is the default for modern scientific Python stacks (glibc 2.39, Python 3.12). Use Ubuntu 22.04 only if a specific tool requires older glibc. Never use Alpine — scientific Python packages frequently require glibc.

**`apt_packages`:** Include system libraries required by pip packages, not just the pip packages themselves. OVITO needs osmesa and libgl. LAMMPS needs libfftw3-dev and libpng-dev. Missing apt packages produce confusing pip install failures or runtime crashes. When in doubt, include the library — apt packages are cheap to install.

**`special_installs`:** Use for anything that cannot be pip-installed correctly — source builds, binary downloads, custom compilations. Always include a `reason` field explaining WHY the special install is needed. The installer uses this reason to determine the correct build flags.

**`env_vars`:** Include ALL runtime environment variables the container needs. The executor reads this field directly to pass `-e` flags to `docker run`. If an env var is missing here, it will not be set when the workflow runs.

**`pip_packages`:** List exact package names as they appear on PyPI. Include version constraints where they matter (e.g., `parsl>=2024.0.0`). Do not list packages that are covered by `special_installs`.

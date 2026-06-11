---
name: knowledge/local_machine
description: >
  Knowledge skill for running HPC-described scientific workflows in a conda
  environment on a local machine, workstation, or Linux cluster node. Explains
  the HPC-to-local gap and how to translate every dependency for conda execution.
  Load when goal mentions "local", "my machine", "locally", "conda", or "workstation".
---

# Local Machine Execution — Knowledge Skill

## When to Use This Skill

Load when the workflow runs in a **conda environment** on a local machine, workstation, or cluster node. The execution model is:

```
conda run -n maw_sandbox python3 builds/workflow.py --data-dir /abs/path/data --work-dir /abs/path/work
```

Do NOT load this skill for native HPC runs with real SLURM/MPI parallelism across multiple nodes.

---

## The Core Gap

Scientific papers describe workflows built for HPC clusters: hundreds of CPU cores, distributed memory across nodes, high-speed network fabric, job schedulers, and shared parallel filesystems. MAW runs these workflows inside a single conda environment (`maw_sandbox`) on a single node.

The translation goal: run the same scientific computation with the same tools in serial or thread-parallel mode inside one conda env.

---

## Why HPC Software Needs Translation

### MPI

HPC papers run `mpirun -np 128 lammps -in script.in`. Many scientific tools call `MPI_Init` on import even without explicit parallelization — this crashes without an MPI daemon.

**Fix:** Build from source with MPI disabled (`-DBUILD_MPI=off` for LAMMPS). Use the serial Python API.

### Job Schedulers (SLURM, PBS)

Papers submit work with `sbatch job.sh`. No scheduler in a single-node conda env.

**Fix:** Use Parsl's `LocalProvider` + `HighThroughputExecutor`. Same task-graph model, no scheduler.

### Module System (`module load`)

Each `module load X` is a signal: X needs to be installed in the conda env or via `install.sh`.

### Shared Filesystems

`/scratch/`, `/project/` HPC paths do not exist locally.

**Fix:** Use real host paths. The executor passes `--data-dir` and `--work-dir` as actual host paths.

---

## Translation Table

| Paper says | Conda equivalent |
|---|---|
| `mpirun -np 8 lammps -in script.in` | `from lammps import lammps; lmp = lammps(cmdargs=["-screen","none"]); lmp.file(...)` |
| `module load lammps/2023` | LAMMPS source build in `install.sh` with `$CONDA_PREFIX` as install prefix |
| `#SBATCH --nodes=4` | Ignore — single node |
| `parsl.load(config)` with `SlurmProvider` | `LocalProvider` + `HighThroughputExecutor` |
| `from mpi4py import MPI` | Remove — use Parsl local workers |
| `module load ovito` | `pip: ovito` in `environment.yml` |
| `/scratch/user/data/` | `--data-dir` argument (real host path, passed by executor) |
| `LD_LIBRARY_PATH=/usr/local/lib` | `LD_LIBRARY_PATH=$CONDA_PREFIX/lib` (passed at runtime by executor) |
| `pip3 install x --break-system-packages` | `pip install x` — no flag needed inside conda |

---

## Known Runtime Gotchas

### Headless Rendering (OVITO, matplotlib, any OpenGL tool)

Visualization tools expect a display server. Without one: `Cannot connect to display`, `osmesa: failed to initialize`.

**Fix:** OSMesa (software rasterizer). Required conda-forge packages:
- `mesalib` — provides OSMesa
- `glib`, `xkeyboard-config`, `dbus` — required by OVITO at import

Required env vars (in `stack_decision.env_vars` — executor passes at runtime via subprocess env):
- `LIBGL_ALWAYS_SOFTWARE=1`
- `PYOPENGL_PLATFORM=osmesa`
- `OVITO_GUI_MODE=0`

Inside any worker function that uses matplotlib: `matplotlib.use("Agg")` before `import matplotlib.pyplot`.

### The LAMMPS MPI Trap

`pip install lammps` wheel calls `MPI_Init` on import — crashes without an MPI daemon.

**Fix:** Source build in `install.sh`:
```bash
cmake ../cmake \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX=$CONDA_PREFIX \
  -DCMAKE_PREFIX_PATH=$CONDA_PREFIX \
  -DBUILD_MPI=off \
  -DFFTW3_ROOT=$CONDA_PREFIX
make -j$(nproc) && make install
```

### LD_LIBRARY_PATH for Source-Built Libraries

LAMMPS installed into `$CONDA_PREFIX` puts `.so` files in `$CONDA_PREFIX/lib`. Set `LD_LIBRARY_PATH=$CONDA_PREFIX/lib` in `stack_decision.env_vars`. Executor passes it at runtime.

Inside Parsl worker functions, also set it explicitly before importing LAMMPS (workers may not inherit all env vars from the parent process):
```python
import os
os.environ.setdefault('LD_LIBRARY_PATH', os.path.join(os.environ.get('CONDA_PREFIX', '/usr'), 'lib'))
from lammps import lammps
```

### No `--break-system-packages`

Inside a conda env, `pip install` never needs `--break-system-packages`. Remove this flag — it causes warnings in conda.

---

## What a Complete `stack_decision` Looks Like (do not follow this exactly. this is simply a possible scenario. YOUR TAKEAWAY: Recognizing patterns and visually what it should look like.)

```json
{
  "base_image": "maw_sandbox",
  "apt_packages": [
    "build-essential", "cmake", "wget", "git",
    "libfftw3-dev", "libpng-dev", "libjpeg-dev",
    "libosmesa6", "libgl1", "libegl1", "libopengl0",
    "libglib2.0-0", "libxkbcommon0", "libdbus-1-3"
  ],
  "pip_packages": ["ovito", "parsl>=2024.0.0", "numpy", "matplotlib", "Pillow"],
  "special_installs": [
    {
      "name": "lammps_source_build_no_mpi",
      "reason": "pip lammps wheel calls MPI_Init on import and crashes; source build with BUILD_MPI=off has zero MPI dependency"
    }
  ],
  "env_vars": {
    "LIBGL_ALWAYS_SOFTWARE": "1",
    "PYOPENGL_PLATFORM": "osmesa",
    "OVITO_GUI_MODE": "0",
    "LD_LIBRARY_PATH": "$CONDA_PREFIX/lib"
  },
  "workdir": "."
}
```

### Field rules

**`base_image`:** Set to `"maw_sandbox"`. There is no base container image — the installer creates a conda env named `maw_sandbox`.

**`apt_packages`:** List Linux package names. The installer translates each to its conda-forge equivalent (see installer base skill for the translation table). Missing entries → missing conda packages → build or runtime failures.

**`special_installs`:** Anything that cannot be pip-installed correctly — source builds, binary downloads. Include a `reason` field. Installer generates `install.sh` from this.

**`env_vars`:** ALL runtime variables the workflow needs. Executor reads this field and passes them via subprocess env dict at runtime. Do NOT put env vars in `environment.yml`.

**`workdir`:** Set to `"."`. No container path convention. Executor passes real host paths via `--data-dir` and `--work-dir`.

**`pip_packages`:** PyPI package names with version constraints. Do not list packages covered by `special_installs`.

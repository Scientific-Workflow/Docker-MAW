---
name: use_cases/molecular_nucleation/installer
description: >
  Installer knowledge for molecular nucleation workflows. Provides verified build
  recipes for LAMMPS source build (no MPI) inside a conda environment, OSMesa
  headless setup, and platform gotchas. The installer generates environment.yml +
  install.sh from the planner's stack_decision — this skill provides the
  domain-specific build knowledge to do that correctly.
---

# Molecular Nucleation — Installer Skill

Domain-specific conda environment construction knowledge for workflows that use LAMMPS, OVITO, and Parsl.

---

## Your Job

The planner has already decided what to install. Your job is to write a correct `environment.yml` and `install.sh` that builds it. This skill provides the verified build recipes and known platform gotchas for the tools in this domain.

---

## LAMMPS Source Build (no MPI) — install.sh

When `stack_decision.special_installs` includes `lammps_source_build_no_mpi`:

**Why source build:** The `pip install lammps` wheel calls `MPI_Init` on import even in serial mode. Source build with `-DBUILD_MPI=off` has zero MPI dependency and runs reliably in a conda env without a running MPI daemon.

**Verified install.sh sequence:**

```bash
#!/bin/bash
set -e

cd /tmp
wget -q https://github.com/lammps/lammps/archive/refs/tags/stable_2Aug2023_update3.tar.gz
tar xzf stable_2Aug2023_update3.tar.gz
cd lammps-stable_2Aug2023_update3
mkdir build && cd build
cmake ../cmake \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX=$CONDA_PREFIX \
  -DCMAKE_PREFIX_PATH=$CONDA_PREFIX \
  -DBUILD_MPI=off \
  -DBUILD_OMP=off \
  -DBUILD_SHARED_LIBS=on \
  -DLAMMPS_EXCEPTIONS=on \
  -DPKG_MANYBODY=on \
  -DPKG_MOLECULE=on \
  -DPKG_KSPACE=on \
  -DPKG_RIGID=on \
  -DPKG_PYTHON=on \
  -DFFT=FFTW3 \
  -DFFTW3_ROOT=$CONDA_PREFIX
make -j$(nproc)
make install
cd /tmp/lammps-stable_2Aug2023_update3/python
pip install .
rm -rf /tmp/lammps-stable_2Aug2023_update3 /tmp/stable_2Aug2023_update3.tar.gz
```

**`-DCMAKE_INSTALL_PREFIX=$CONDA_PREFIX` is mandatory.** Without it, cmake defaults to `/root/.local` or `/usr/local`, outside the conda env. `liblammps.so` will not be found by Python inside the env.

**`-DCMAKE_PREFIX_PATH=$CONDA_PREFIX` is mandatory.** This tells cmake to find FFTW3, libpng, zlib, etc. from the conda env rather than the system. Without it, cmake finds wrong or missing libraries.

**`-DFFTW3_ROOT=$CONDA_PREFIX`** ensures the FFTW3 cmake module finds the conda-forge `fftw` package.

**`pip install .` (not `pip install . --break-system-packages`).** In a conda env, pip does not need `--break-system-packages`. Adding it causes a warning or error.

**After install, `liblammps.so` is in `$CONDA_PREFIX/lib`.** The executor passes `LD_LIBRARY_PATH` via env_vars, so no post-install ldconfig needed.

---

## Required environment.yml entries for LAMMPS build

```yaml
dependencies:
  - python=3.11
  - cmake
  - fftw
  - libpng
  - libjpeg-turbo
  - zlib
  - gcc
  - gxx_linux-64
  - make
  - wget
  - git
  - pip
  - pip:
    - ovito
    - "parsl>=2024.0.0"
    - numpy
    - matplotlib
    - Pillow
```

`mesalib` for headless rendering (covers OSMesa):
```yaml
  - mesalib
  - glib
```

---

## Headless Rendering (OVITO + matplotlib)

When `stack_decision.env_vars` includes `LIBGL_ALWAYS_SOFTWARE`, `PYOPENGL_PLATFORM`, or `OVITO_GUI_MODE`:

Add to `environment.yml` dependencies:
```yaml
  - mesalib
  - glib
  - dbus
  - xkeyboard-config
```

The env_vars themselves (`LIBGL_ALWAYS_SOFTWARE=1`, `PYOPENGL_PLATFORM=osmesa`, `OVITO_GUI_MODE=0`) are NOT written into `environment.yml` — they are passed by the executor at runtime via the subprocess env dict.

---

## Hash-Based Rebuild Skip

The Phase 2 build is skipped if the MD5 hash of (`environment.yml` + `install.sh`) matches `builds/.env_spec_hash` AND the `maw_sandbox` env exists. This means the ~20 minute LAMMPS build only runs when the spec actually changes.

---

## env_vars in stack_decision

`env_vars` (e.g., `LD_LIBRARY_PATH=/path`) are NOT added to `environment.yml`. They are passed by the executor to `conda run` via `subprocess env=` dict. Include them in `stack_decision` for the executor to pick up — the installer does not touch them.

The one exception: `LD_LIBRARY_PATH=$CONDA_PREFIX/lib` may need to be set at workflow runtime so LAMMPS's `liblammps.so` can be found. The planner should include `"LD_LIBRARY_PATH": "$CONDA_PREFIX/lib"` in `env_vars` — the executor expands environment variables in the subprocess env automatically.

---
name: use_cases/molecular_nucleation/installer
description: >
  Installer knowledge for molecular nucleation workflows. Provides the verified LAMMPS
  source build recipe (cmake flags, package list, install sequence) and OVITO headless
  rendering packages. Use this for the exact HOW — the WHY is in knowledge/local_machine.
---

# Molecular Nucleation — Installer Skill

Verified build recipes for LAMMPS, OVITO, and Parsl in the `maw_sandbox` conda env.

---

## LAMMPS Source Build — install.sh Recipe

When `stack_decision.special_installs` includes `lammps_source_build_no_mpi`, use this exact sequence:

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

**Flag notes:**
- `-DCMAKE_INSTALL_PREFIX=$CONDA_PREFIX` — mandatory; without it cmake installs outside the conda env and `liblammps.so` won't be found
- `-DCMAKE_PREFIX_PATH=$CONDA_PREFIX` — mandatory; tells cmake to find FFTW3, libpng, zlib from conda-forge rather than the system
- `-DFFTW3_ROOT=$CONDA_PREFIX` — ensures the FFTW3 cmake module finds the conda-forge `fftw` package
- `-DBUILD_MPI=off` — mandatory; see `knowledge/local_machine` for why
- `pip install .` — no `--break-system-packages` flag; not needed inside a conda env

---

## Required environment.yml — LAMMPS Build Dependencies

```yaml
name: maw_sandbox
channels:
  - conda-forge
  - defaults
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
  - mesalib
  - glib
  - dbus
  - xkeyboard-config
  - pip:
    - ovito
    - "parsl>=2024.0.0"
    - numpy
    - matplotlib
    - Pillow
```

`mesalib`, `glib`, `dbus`, `xkeyboard-config` are required by OVITO at import for headless rendering. Missing any of these causes OVITO to crash on import even when rendering is disabled.

---

## env_vars — What Goes Where

`env_vars` from `stack_decision` are passed by the executor at runtime — the installer does NOT write them into `environment.yml`. For this workflow the required runtime vars are:

| Var | Value | Purpose |
|---|---|---|
| `LIBGL_ALWAYS_SOFTWARE` | `1` | Forces OSMesa software rendering |
| `PYOPENGL_PLATFORM` | `osmesa` | Tells PyOpenGL to use OSMesa backend |
| `OVITO_GUI_MODE` | `0` | Disables OVITO GUI entirely |
| `LD_LIBRARY_PATH` | `$CONDA_PREFIX/lib` | Makes `liblammps.so` findable after source build |

These must be in `stack_decision.env_vars` so the executor picks them up. Do not put them in `environment.yml`.

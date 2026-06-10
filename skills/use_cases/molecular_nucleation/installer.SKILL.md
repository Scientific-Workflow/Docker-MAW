---
name: use_cases/molecular_nucleation/installer
description: >
  Installer knowledge for molecular nucleation workflows. Provides verified build
  recipes for LAMMPS source build without MPI, osmesa headless setup, and platform
  gotchas. The installer formats a Dockerfile from the planner's stack_decision —
  this skill provides the domain-specific build knowledge to do that correctly.
---

# Molecular Nucleation — Installer Skill

Domain-specific Dockerfile construction knowledge for workflows that use LAMMPS, OVITO, and Parsl.

---

## Your Job

The planner has already decided what to install. Your job is to write a correct Dockerfile that builds it. This skill provides the verified build recipes and known platform gotchas for the tools in this domain. Use them when the `stack_decision` calls for them — do not use them when it does not.

---

## LAMMPS Source Build (no MPI)

When `stack_decision.special_installs` includes `lammps_source_build_no_mpi`:

**Why source build:** The `pip install lammps` wheel calls `MPI_Init` on import even in serial mode. Without `orted` in PATH, this produces `ORTE_ERROR_LOG` / `orte_init failure` before any simulation runs. Source build with `-DBUILD_MPI=off` has zero MPI dependency.

**Verified build sequence:**

```dockerfile
# LAMMPS from source — stable_2Aug2023_update3, no MPI
RUN cd /tmp && \
    wget -q https://github.com/lammps/lammps/archive/refs/tags/stable_2Aug2023_update3.tar.gz && \
    tar xzf stable_2Aug2023_update3.tar.gz && \
    cd lammps-stable_2Aug2023_update3 && \
    mkdir build && cd build && \
    cmake ../cmake \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/usr/local \
      -DBUILD_MPI=off \
      -DBUILD_OMP=off \
      -DBUILD_SHARED_LIBS=on \
      -DLAMMPS_EXCEPTIONS=on \
      -DPKG_MANYBODY=on \
      -DPKG_MOLECULE=on \
      -DPKG_KSPACE=on \
      -DPKG_RIGID=on \
      -DPKG_PYTHON=on \
      -DFFT=FFTW3 && \
    make -j$(nproc) && \
    make install && \
    echo '/usr/local/lib' > /etc/ld.so.conf.d/local.conf && ldconfig && \
    cd /tmp/lammps-stable_2Aug2023_update3/python && \
    PYTHONNOUSERSITE=1 pip3 install . --break-system-packages && \
    rm -rf /tmp/lammps-stable_2Aug2023_update3 /tmp/stable_2Aug2023_update3.tar.gz

ENV LD_LIBRARY_PATH=/usr/local/lib
```

**Required apt packages for this build:** `build-essential`, `cmake`, `wget`, `git`, `libfftw3-dev`, `libpng-dev`, `libjpeg-dev`, `python3-dev`

**`-DCMAKE_INSTALL_PREFIX=/usr/local` is mandatory.** Without it, cmake defaults to `/root/.local` as the install path, so `liblammps.so` lands in `/root/.local/lib/` instead of `/usr/local/lib/`. Nothing downstream finds it.

**`PYTHONNOUSERSITE=1` on the pip install step is mandatory.** Without it, pip defaults to a user-local install (`~/.local/lib`) even as root, putting the Python package where Python can't find it. `PYTHONNOUSERSITE=1` forces pip to install to the system `dist-packages` path. Do NOT use `--prefix=/usr/local` — that installs to `site-packages` which Ubuntu Python does not search.

**`ldconfig` after `make install` is mandatory** — writes `/usr/local/lib` into the system linker cache so `liblammps.so` is discoverable by any subprocess regardless of environment variable inheritance.

**Do NOT write** `ENV LD_LIBRARY_PATH=...:$LD_LIBRARY_PATH` — the self-reference evaluates to empty string and creates a malformed path.

---

## Headless Rendering (OVITO + matplotlib)

When `stack_decision.env_vars` includes `LIBGL_ALWAYS_SOFTWARE`, `PYOPENGL_PLATFORM`, or `OVITO_GUI_MODE`:

**Required apt packages:** `libosmesa6`, `libgl1`, `libegl1`, `libopengl0`, `libglib2.0-0`, `libxkbcommon0`, `libxkbcommon-x11-0`, `libdbus-1-3`, `libxcb-icccm4`, `libxcb-image0`, `libxcb-keysyms1`, `libxcb-render-util0`, `libxcb-xinerama0`, `libxcb-xkb1`, `libxrender1`, `libxi6`, `libxtst6`

**ENV instructions to add:**
```dockerfile
ENV LIBGL_ALWAYS_SOFTWARE=1
ENV PYOPENGL_PLATFORM=osmesa
ENV OVITO_GUI_MODE=0
```

---

## Ubuntu 24.04 pip Rule

Every `pip3 install` line must end with `--break-system-packages`. Ubuntu 24.04 enforces PEP 668 (externally managed Python). Without this flag, pip refuses to install.

---

## Hash-Based Rebuild Skip

The Phase 2 build is skipped if the Dockerfile MD5 matches the hash stored in `builds/.dockerfile_hash` AND the image already exists. This means the ~20 minute LAMMPS build only runs when the Dockerfile actually changes. Do not manually delete this file.

---

## Dockerfile Layer Ordering

For this domain, the correct ordering is:
1. `FROM` base image
2. `ENV DEBIAN_FRONTEND=noninteractive`
3. Single `RUN apt-get update && apt-get install -y ... && rm -rf /var/lib/apt/lists/*`
4. pip installs (standard packages)
5. LAMMPS source build (if in special_installs) — this takes the most time, put it late so earlier layers cache
6. `WORKDIR`
7. `ENV` runtime variables (LIBGL, LD_LIBRARY_PATH, etc.)

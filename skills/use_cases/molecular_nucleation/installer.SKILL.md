---
name: use_cases/molecular_nucleation/installer
description: >
  Installer behavior for the molecular nucleation project. The Docker image is pre-built
  from source; the installer reads the existing Dockerfile from disk and the orchestrator
  auto-approves. Covers the source-built LAMMPS Dockerfile requirements and image reuse logic.
---

# Molecular Nucleation — Installer Skill

The sandbox image (`maw-sandbox:latest`) is pre-built. The installer's job is minimal: return the existing Dockerfile for approval, then confirm the image exists.

---

## Current Installer Behavior

1. **Phase 1:** Read `builds/Dockerfile` from disk. Return it as `dockerfile`. Do NOT call an LLM to generate a new one.
2. **Orchestrator:** Always approves immediately (sets `dockerfile_approved=True`).
3. **Phase 2:** Check if `maw-sandbox:latest` already exists. If yes, skip `docker build` and return the tag. If no, run `docker build -t maw-sandbox:latest builds/` (~20 min first time).

---

## Dockerfile Requirements (for review/rebuild if ever needed)

```dockerfile
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive

# System deps — NO OpenMPI, no MPI anything
RUN apt-get update && apt-get install -y \
    python3 python3-pip python3-dev build-essential cmake wget git \
    libfftw3-dev libpng-dev libjpeg-dev libosmesa6 libgl1 libegl1 libopengl0 \
    libglib2.0-0 libxkbcommon0 libxkbcommon-x11-0 libdbus-1-3 \
    libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 \
    libxcb-xinerama0 libxcb-xkb1 libxrender1 libxi6 libxtst6 \
    && rm -rf /var/lib/apt/lists/*

# LAMMPS built from source WITHOUT MPI — stable_2Aug2023_update3
# cmake flags: BUILD_MPI=off BUILD_OMP=off BUILD_SHARED_LIBS=on
#   LAMMPS_EXCEPTIONS=on PKG_MANYBODY PKG_MOLECULE PKG_KSPACE PKG_RIGID PKG_PYTHON FFT=FFTW3
# Installed to /usr/local via make install

ENV LD_LIBRARY_PATH=/usr/local/lib
# (no $LD_LIBRARY_PATH self-reference — causes UndefinedVar linting error)

# LAMMPS Python bindings from source tarball: pip3 install . --break-system-packages
# Verify: python3 -c "from lammps import lammps; lmp=lammps(...); print('LAMMPS OK'); lmp.close()"

RUN pip3 install ovito --break-system-packages
RUN pip3 install 'parsl>=2024.0.0' numpy matplotlib Pillow --break-system-packages

WORKDIR /app
ENV LIBGL_ALWAYS_SOFTWARE=1
ENV PYOPENGL_PLATFORM=osmesa
ENV OVITO_GUI_MODE=0
```

---

## Why Source-Build (not pip lammps wheel)

The pip `lammps` wheel calls `MPI_Init` on import even in serial mode. ORTE's singleton mode then searches PATH for `orted` (in `/usr/lib/openmpi/bin/` — not in PATH by default). This causes:
- `WorkerLost` errors in Parsl workers
- `ORTE_ERROR_LOG` / `orte_init failure` in stderr
- Crash before any simulation runs

Source build with `BUILD_MPI=off` has zero MPI dependency. No init, no crash.

---

## Notes

- Rebuild is only needed if `builds/Dockerfile` changes
- First build takes ~20 min; all subsequent runs reuse the cached image
- `builds/.dockerfile_hash` stores the MD5 of the last built Dockerfile for skip detection

---
name: agents/installer
description: >
  Base skill for the installer agent. Covers the two-phase conda environment spec
  generation and build process: Phase 1 generates environment.yml + install.sh from
  the planner's stack_decision, Phase 2 runs conda env create and install.sh.
  Covers approval flow, skip logic, and formatting rules.
---

# Installer Agent — Base Skill

Generates the `maw_sandbox` conda environment. Phase 1 produces `environment.yml` and `install.sh` from the planner's `stack_decision` for orchestrator review. Phase 2 creates the environment — skipped if the env already exists and the spec is unchanged.

---

## Your Job

You receive a fully-specified `stack_decision` from the planner. Your job is to translate it into a correct `environment.yml` and `install.sh`. You do not decide what to install — the planner already decided that. You decide how to install it correctly for conda.

Pair this base skill with the use-case installer skill for domain-specific build patterns (source builds, known platform gotchas).

---

## Two-Phase Flow

### Phase 1 — Generate the env spec

Read `stack_decision` from state. Generate:

1. `environment.yml` — implements `apt_packages` (as conda-forge equivalents), `pip_packages`, python version
2. `install.sh` — implements `special_installs` as bash commands running inside the conda env

Return: `{"environment_yml": <content>, "install_script": <content>, "current_step": "installer_env_spec_pending_approval"}`

### Phase 2 — Build the environment

Only runs after the orchestrator sets `env_spec_approved=True`.

Check if the env can be skipped before building:
- MD5 hash of `environment.yml` + `install.sh` contents matches `builds/.env_spec_hash`
- AND `maw_sandbox` appears in `conda env list`

If both true → skip and return `installer_complete`.

If env does NOT exist: `conda env create -n maw_sandbox --file environment.yml`
If env EXISTS but changed: `conda env update -n maw_sandbox --file environment.yml --prune`

After env create/update, if `install.sh` has content beyond the shebang:
`conda run -n maw_sandbox --no-capture-output bash builds/install.sh`

Return: `{"current_step": "installer_complete"}`

---

## environment.yml Format

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
  - mesalib
  - glib
  - git
  - wget
  - pip
  - pip:
    - ovito
    - "parsl>=2024.0.0"
    - numpy
    - matplotlib
    - Pillow
```

---

## Platform Awareness — Windows vs Linux

**`gxx_linux-64` is Linux-only.** If the target machine is Windows, `conda env create` will fail immediately because this package does not exist for the `win-64` platform.

Rule: **source builds (LAMMPS, anything in install.sh) do not work natively on Windows.** `bash`, `/tmp`, `make -j$(nproc)`, and Linux compiler packages all assume a Linux environment.

| Situation | What to do |
|---|---|
| Target is Linux (LCRC, WSL, any Linux machine) | Use `gcc`, `gxx_linux-64`, `make` — normal flow |
| Target is Windows (native PowerShell/cmd) | Do NOT generate install.sh with bash commands. Warn the user that source builds require WSL or a Linux machine. |
| Target is Windows + WSL | Treat as Linux — run `python agent.py` from inside WSL, not from PowerShell |

If the goal says "local machine" and you cannot determine the OS, check `platform.system()` output in state or ask the orchestrator to note it. When in doubt, generate Linux-compatible packages — they are correct for LCRC and any Linux host.

---

### apt_packages → conda-forge translation table

| apt_packages entry | conda-forge equivalent |
|---|---|
| `cmake` | `cmake` |
| `build-essential` | `gcc`, `gxx_linux-64`, `make` (**Linux only** — see Platform Awareness above) |
| `libfftw3-dev` | `fftw` |
| `libpng-dev` | `libpng` |
| `libjpeg-dev` | `libjpeg-turbo` |
| `zlib1g-dev` | `zlib` |
| `libosmesa6` | `mesalib` |
| `libgl1` / `libegl1` / `libopengl0` | covered by `mesalib` |
| `libglib2.0-0` | `glib` |
| `libxkbcommon0` | `xkeyboard-config` |
| `libdbus-1-3` | `dbus` |
| `wget` | `wget` |
| `git` | `git` |
| `python3`, `python3-pip`, `python3-dev` | covered by `python=3.11` |

`base_image` and `workdir` from `stack_decision` are ignored — no container.

---

## install.sh Format

```bash
#!/bin/bash
set -e
# $CONDA_PREFIX is set by conda run and points to the maw_sandbox environment
# Use it for all install prefixes and library paths

# Example LAMMPS source build:
cd /tmp
wget -q https://...tarball...
tar xzf ...
cd lammps-.../
mkdir build && cd build
cmake ../cmake \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX=$CONDA_PREFIX \
  -DCMAKE_PREFIX_PATH=$CONDA_PREFIX \
  ...
make -j$(nproc) && make install
cd /tmp/lammps-.../python
pip install .
rm -rf /tmp/lammps-...
```

Key rules:
- Use `$CONDA_PREFIX` as install prefix — NOT `/usr/local`
- No `--break-system-packages` needed in conda pip installs
- Return empty string `""` if `special_installs` is empty

---

## Key Rules

- Never proceed to Phase 2 without `env_spec_approved=True` in state
- Always check existing env and hash before building — LAMMPS builds take 20+ minutes
- Do not add packages not in `stack_decision`
- `env_vars` from `stack_decision` are NOT set in environment.yml — they are passed by the executor at runtime via subprocess env

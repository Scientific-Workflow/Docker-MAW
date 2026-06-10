# MAW — Issues and Solutions Log

Running record of bugs encountered, root causes, and fixes applied.

---

## 0. Codegen hardcodes data filenames — fails for any paper other than the original

**Symptom:** When running a different molecular nucleation paper whose data files have different names (e.g., `SPC_E.tersoff` instead of `AW.tersoff`, `data.lammps` instead of `data.init`), the generated workflow crashes with `FileNotFoundError` because the codegen hardcoded the original filenames.

**Root cause:** `skills/use_cases/molecular_nucleation/codegen.SKILL.md` had hardcoded `["data.init", "AW.tersoff"]` in the copy step and `in.watbox` as the hardcoded input script name. The planner had no instruction to match paper-described files against what was actually in `data/`.

**Fix:**
1. `agent.py`: at startup, scan `data/` and store filenames in `AgentState.data_files`. Inject the list into the planner's human prompt as "Available data files: ...".
2. `skills/use_cases/molecular_nucleation/codegen.SKILL.md`: replaced hardcoded copy list with `for fname in os.listdir(data_dir)` — copies everything present. Removed hardcoded `in.watbox` reference; codegen now uses whatever filename the planner specifies in tasks.
3. `skills/use_cases/molecular_nucleation/planner.SKILL.md`: added "Data Files" section instructing the planner to match paper-described filenames against the available files list and put exact matched filenames in tasks.

---

## 1. Orchestrator loops on planner — claims `apt_packages` and `env_vars` missing

**Symptom:** Orchestrator repeatedly routes back to planner saying `apt_packages` and `env_vars` are absent, even though the planner output panel clearly shows them populated.

**Root cause:** The orchestrator's human prompt only included `base_image`, `pip_packages`, and `special_installs` from `stack_decision`. `apt_packages` and `env_vars` were never serialized into the prompt, so the LLM literally could not see them.

**Fix:** Updated `orchestrator()` in `agent.py` (lines ~318-327) to serialize all six `stack_decision` fields into the human prompt.

---

## 2. `--break-system-packages` flag placed after `&& \` continuation

**Symptom:** Dockerfile contained `pip3 install . && \ --break-system-packages` — a valid-looking line that either fails silently or causes Docker to parse the next line as a new instruction.

**Root cause:** The pip guardrail in `installer()` stripped the flag then appended it with `.rstrip() + ' --break-system-packages'`, which put it after any trailing `&&` or `\` continuation rather than before it.

**Fix (agent.py):** Guardrail now detects trailing `&&`, `&& \`, and bare `\` continuations and inserts the flag before them, not after. Regex: `r'(\s*&&\s*\\?\s*|\s+\\)$'`.

**Fix (skill file):** `skills/agents/installer.SKILL.md` now has an explicit rule: flag must appear before `&&`, not after it.

---

## 3. MPI symlink injected for no-MPI source build — crashes Docker build

**Symptom:** Docker build fails during the injected MPI symlink step. `find /usr/lib -name "libmpi.so.*"` returns empty because no MPI packages are installed, then `ln -sf "" "./libmpi.so.12"` fails.

**Root cause:** The MPI symlink guardrail condition (`if any("lammps" in x ...)`) fired on `lammps_source_build_no_mpi`. That build intentionally has no MPI — the symlink only applies to the pip lammps wheel, which links against `libmpi.so.12` on a system that ships `.so.40`.

**Fix:** Changed condition to `if any("lammps" in x and "no_mpi" not in x ...)` so the symlink is only injected for pip wheel installs.

---

## 4. `liblammps.so` not found at runtime — Parsl worker can't load shared library

**Symptom:** `OSError: liblammps.so: cannot open shared object file: No such file or directory` inside the Parsl worker, even though `ENV LD_LIBRARY_PATH=/usr/local/lib` is set in the Dockerfile.

**Root cause:** `LD_LIBRARY_PATH` as a Dockerfile `ENV` is not reliably inherited by Parsl's `HighThroughputExecutor` worker subprocesses. Additionally, `ldconfig` was not being run after `make install`, so the system linker cache didn't know about `/usr/local/lib/liblammps.so`.

**Fix (skill file):** `skills/use_cases/molecular_nucleation/installer.SKILL.md` build recipe updated to run `echo '/usr/local/lib' > /etc/ld.so.conf.d/local.conf && ldconfig` after `make install`. This puts the library path in the system linker cache permanently, independent of environment variable inheritance.

---

## 5. Parsl certificates directory permissions error on Windows/WSL Docker

**Symptom:** `OSError: The certificates directory must be private: /app/builds/runinfo/002/local_htex/certificates` — Parsl refuses to start because the certificates directory doesn't have `700` permissions.

**Root cause:** Parsl writes its internal runtime files (ZMQ certificates, worker logs) to `runinfo/` in the current working directory, which is `/app/builds/` — a bind mount from the Windows host. Windows filesystems (NTFS via WSL2 9P driver) cannot represent Linux `700` permissions; all directories come through as `0755`, which Parsl rejects for security reasons.

**Platform scope:** Windows + Docker Desktop, macOS + Docker Desktop. Not an issue on native Linux where bind mounts support `700`.

**Fix (skill file):** `skills/agents/codegen.SKILL.md` Parsl config block updated to include `run_dir='/tmp/parsl_runinfo'`. This moves all Parsl internal state to a native tmpfs directory inside the container where permissions work correctly on all platforms.

**Why this is architecture-agnostic:** `/tmp/` is always a native container filesystem regardless of the host. The fix is correct and harmless on native Linux too — Parsl runtime bookkeeping should not live in the version-controlled `builds/` directory anyway.

---

## 6. cmake defaults install prefix to `/root/.local` instead of `/usr/local`

**Symptom:** `liblammps.so: cannot open shared object file` at runtime. `find /usr/local/lib -name "liblammps*"` returns nothing. `find / -name "liblammps*"` finds it at `/root/.local/lib/liblammps.so`.

**Root cause:** Without `-DCMAKE_INSTALL_PREFIX`, cmake on this system defaults the install path to `/root/.local` instead of `/usr/local`. `make install` puts `liblammps.so` in `/root/.local/lib/`, but every downstream reference (`ENV LD_LIBRARY_PATH`, `ld.so.conf.d`, codegen `os.environ`) points at `/usr/local/lib/`. The library is correctly built but never reachable.

**Fix:** Added `-DCMAKE_INSTALL_PREFIX=/usr/local` to the cmake command in `skills/use_cases/molecular_nucleation/installer.SKILL.md`. This forces the install path to `/usr/local` on any Linux system regardless of cmake defaults.

---

## 7. pip installs `lammps` Python package to user-local path — `ModuleNotFoundError`

**Symptom:** `ModuleNotFoundError: No module named 'lammps'` inside the Parsl worker, even though `pip3 install .` appeared to succeed during the Docker build.

**Root cause:** `pip3 install . --break-system-packages` defaults to a user-local install (`~/.local/lib/python3.12/dist-packages/`) even when running as root. Additionally, using `--prefix=/usr/local` (an earlier attempted fix) puts the package in `site-packages` instead of `dist-packages` — Ubuntu Python searches `dist-packages` only. Result: the Python package installs somewhere Python can't find it.

**Fix:** Added `PYTHONNOUSERSITE=1` before the pip install step in the installer skill. This prevents pip from using the user-local path and forces installation to the system `dist-packages` path. Combined with `-DCMAKE_INSTALL_PREFIX=/usr/local`, both the shared library and the Python package land in `/usr/local` where all path references point.

---

## 8. Docker build output swallowed — can't see where libraries install

**Symptom:** `docker build` runs silently. On failure, only the last 3000 chars of stderr are shown. Can't see cmake's `Install path:` line or where pip puts packages.

**Root cause:** `subprocess.run(..., capture_output=True)` in `installer()` suppressed all build output.

**Fix:** Removed `capture_output=True` from the `subprocess.run` call in `agent.py`. Build output now streams live to the terminal.

---

## 10. Codegen omits `run_dir` from Parsl config — certificates error recurs

**Symptom:** `OSError: The certificates directory must be private` keeps appearing even after `run_dir='/tmp/parsl_runinfo'` was added to the skill file, because codegen regenerates the config without it.

**Root cause:** The `CRITICAL` note in `agents/codegen.SKILL.md` only warned about adding extra kwargs — it didn't warn about *omitting* `run_dir`. Codegen treated it as an optional kwarg and dropped it.

**Fix:** Rewrote the CRITICAL note to explicitly state `run_dir` is mandatory and explain why (bind-mounted directories can't have `700` permissions; Parsl requires them for certificates).

---

## 11. `liblammps.so` not found in Parsl worker despite `LD_LIBRARY_PATH` in Dockerfile

**Symptom:** `OSError: liblammps.so: cannot open shared object file` inside `run_lammps` even though the Dockerfile sets `ENV LD_LIBRARY_PATH=/usr/local/lib`.

**Root cause:** Parsl's `HighThroughputExecutor` spawns worker subprocesses that don't reliably inherit Docker `ENV` variables. The library is installed correctly but the worker's dynamic linker can't find it.

**Fix (skill file):** `skills/use_cases/molecular_nucleation/codegen.SKILL.md` — added `os.environ.setdefault('LD_LIBRARY_PATH', '/usr/local/lib')` as the first line inside `run_lammps`, before `from lammps import lammps`. This sets the env var explicitly in the worker process regardless of what it inherited.

---

## 12. `pip3 install` on bare `\` continuation — Docker parse error

**Symptom:** `dockerfile parse error on line 27: unknown instruction: ovito` — Docker treated `ovito` as a Dockerfile instruction keyword.

**Root cause:** The guardrail produced `RUN pip3 install \ --break-system-packages` (backslash in the middle of the line, not at the end). In Dockerfile parsing, a line continuation only triggers when `\` is the **last character** on the line. With `\ ` in the middle, Docker ended the `RUN` instruction at that line, then tried to parse `ovito` on the next line as a new Dockerfile instruction.

**Fix:** Extended the guardrail regex to also match bare trailing `\` (not just `&&`) so the flag is inserted before the continuation character in all cases.

---

## 13. Docker build failure crashes entire graph — no recovery possible

**Symptom:** When `docker build` fails (e.g., missing apt dependency, bad source build recipe), the installer raises `RuntimeError`, which propagates up through LangGraph and terminates the entire workflow. The orchestrator never gets a chance to diagnose or retry.

**Root cause:** `installer()` called `raise RuntimeError(...)` on non-zero exit code. LangGraph treats unhandled node exceptions as fatal, so the graph dies immediately with a Python traceback. No state is returned to the orchestrator, no retry loop is possible.

**Observed instance:** Building n2p2 (Neural Network Potential library) failed because `libopenmpi-dev` was not in apt packages — the n2p2 LAMMPS interface (`libnnpif`) requires `mpic++` to compile even when LAMMPS itself uses `BUILD_MPI=off`. The agent crashed instead of self-correcting.

**Fix:**
1. `installer()` Phase 2 now uses `subprocess.Popen` instead of `subprocess.run` — streams output live to terminal AND captures the last 80 lines for error context.
2. On non-zero exit, returns `current_step: "installer_build_failed"` with `build_error` (captured tail) and incremented `build_attempt` counter instead of raising.
3. `orchestrator()` has a new hard override for `installer_build_failed`: routes back to `installer` (which regenerates the Dockerfile in Phase 1 using the error as feedback) for up to 3 attempts. On attempt 3, routes to `end`.
4. The LLM in the orchestrator reads the captured error output and generates specific feedback — e.g., "mpic++ not found → add libopenmpi-dev to apt_packages" — which the installer LLM uses to produce a corrected Dockerfile.

**Why this matters beyond the one-off fix:** Any new paper with unfamiliar dependencies will hit similar build failures. This loop means the system self-heals without a human having to diagnose the Dockerfile each time.

---
name: agents/installer
description: >
  Base skill for the installer agent. Covers the two-phase Dockerfile generation and
  build process: Phase 1 generates a Dockerfile from the planner's stack_decision,
  Phase 2 builds the Docker image. Covers approval flow, skip logic, and formatting rules.
---

# Installer Agent — Base Skill

Generates the sandbox Docker image. Phase 1 produces a Dockerfile from the planner's `stack_decision` for orchestrator review. Phase 2 builds the image — skipped if the image already exists and the Dockerfile is unchanged.

---

## Your Job

You receive a fully-specified `stack_decision` from the planner. Your job is to translate it into a correct, buildable Dockerfile. You do not decide what to install — the planner already decided that. You decide how to install it correctly.

Pair this base skill with the use-case installer skill for domain-specific build patterns (source builds, known platform gotchas).

---

## Two-Phase Flow

### Phase 1 — Generate the Dockerfile

Read `stack_decision` from state. Generate a complete Dockerfile that implements every field:
- `base_image` → `FROM` instruction
- `apt_packages` → single `RUN apt-get update && apt-get install -y` block, clean up apt lists at the end
- `pip_packages` → `RUN pip3 install` with `--break-system-packages` on Ubuntu base images
- `special_installs` → custom `RUN` blocks; consult the use-case installer skill for verified build recipes
- `env_vars` → one `ENV` instruction per variable
- `workdir` → `WORKDIR` instruction

Return: `{"dockerfile": <content>, "current_step": "installer_dockerfile_pending_approval"}`

### Phase 2 — Build the Image

Only runs after the orchestrator sets `dockerfile_approved=True`.

Check if the image can be skipped before building:
```bash
docker images -q <image_tag>
```
Also compare the MD5 hash of the current Dockerfile against the stored hash in `builds/.dockerfile_hash`. If both the image exists AND the hash matches, skip the build and return immediately.

If build is needed: `docker build -t <image_tag> -f builds/Dockerfile <build_context>`

Return: `{"image_tag": <tag>, "current_step": "installer_complete"}`

---

## Dockerfile Formatting Rules

- Always use a single `RUN apt-get update && apt-get install -y ... && rm -rf /var/lib/apt/lists/*` block for system packages — minimizes image layers and cleans apt cache
- Every `pip3 install` line must include `--break-system-packages` **as a flag to pip3 install, before any `&&` continuation** — `pip3 install . --break-system-packages && \` is correct; `pip3 install . && \ --break-system-packages` is wrong and will fail
- `ENV` instructions: one variable per line — do NOT chain multiple vars in one `ENV` instruction
- `ENV LD_LIBRARY_PATH=/usr/local/lib` — do NOT self-reference (`$LD_LIBRARY_PATH`) in this line; it evaluates to empty string at build time
- `WORKDIR` goes after all installs, before `ENV` runtime vars
- Do NOT use `pip3 install --upgrade pip` — on Ubuntu 24.04 this fails because Debian pip has no RECORD file

---

## Key Rules

- Never proceed to Phase 2 without `dockerfile_approved=True` in state
- Always check for existing image and hash before building — rebuilds can take 20+ minutes
- `image_tag` returned must be a non-empty string
- Do not add packages not in `stack_decision` — if a package is missing, the orchestrator will reject and send back for revision
- Do not remove packages from `stack_decision` — trust the planner's specification

---

## Notes

- Uses `coder_llm` for Dockerfile generation
- Ownership: Jacob owns installer(), INSTALLER_PROMPT, InstallerOutput

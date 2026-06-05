---
name: agents/installer
description: >
  Base skill for the installer agent. Documents the two-phase Dockerfile build process:
  Phase 1 reads/generates the Dockerfile, Phase 2 builds the Docker image. Covers the
  image-reuse skip logic and orchestrator approval flow.
---

# Installer Agent — Base Skill

Manages the sandbox Docker image. Phase 1 produces a Dockerfile for orchestrator review; Phase 2 builds the image (skipped if image already exists).

---

## When to Use This Skill

Loaded on every installer() call. Provides the general installer mechanics.

Pair with the use-case installer skill for domain-specific Dockerfile requirements (base image, packages, env vars).

---

## Overview

The installer is a two-phase agent:
- **Phase 1:** Returns a Dockerfile to the orchestrator for review. Sets `current_step="installer_dockerfile_pending_approval"`.
- **Phase 2:** Runs `docker build` only after the orchestrator sets `dockerfile_approved=True`. Skips the build if the image tag already exists locally.

**Current behavior:** The installer reads the existing `builds/Dockerfile` from disk (does not call the LLM to generate one). The orchestrator always auto-approves it.

---

## Step-by-Step Workflow

### Step 1 (Phase 1): Read or generate the Dockerfile

Current implementation reads `builds/Dockerfile` directly from disk and returns it as-is.

Returns:
```python
{"dockerfile": dockerfile_content, "current_step": "installer_dockerfile_pending_approval"}
```

### Step 2: Orchestrator reviews and approves

The orchestrator checks the Dockerfile and sets `dockerfile_approved=True`. Installer is called again.

### Step 3 (Phase 2): Build the Docker image (or skip)

Check if the image already exists before building:
```bash
docker images -q maw-sandbox:latest
```
If the image exists → skip `docker build`, return immediately with `image_tag`.
If not → run `docker build -t maw-sandbox:latest builds/`.

---

## Key Rules and Constraints

- Never proceed to Phase 2 without `dockerfile_approved=True` in state
- Always check for existing image before building — rebuilds take ~20 min
- image_tag returned must be a non-empty string; default is `"maw-sandbox:latest"`

---

## Notes

- Uses `coder_llm` for any LLM calls (though current implementation skips LLM in Phase 1)
- Ownership: Jacob owns installer(), INSTALLER_PROMPT, InstallerOutput

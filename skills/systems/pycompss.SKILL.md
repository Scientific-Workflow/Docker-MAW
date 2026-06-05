---
name: systems/pycompss
description: >
  PyCOMPSs workflow framework — stub for future use. Load this skill when the workflow
  uses @task decorators from the pycompss.api module instead of Parsl.
---

# PyCOMPSs — System Skill (Stub)

PyCOMPSs is a task-based parallel programming model developed at BSC. Tasks are defined with `@task` decorators and managed by the COMPSs runtime.

This skill is a placeholder. Populate with specifics when a PyCOMPSs workflow is first implemented in MAW.

## Key Differences from Parsl

| Feature | Parsl | PyCOMPSs |
|---|---|---|
| Task decorator | `@python_app` | `@task` |
| Config | `parsl.load(Config(...))` | `compss_start()` |
| Future resolution | `.result()` | `compss_wait_on(future)` |
| Worker imports | Inside function body | Module-level OK |

## Notes

- PyCOMPSs requires the COMPSs runtime installed separately
- Not currently in the MAW sandbox Dockerfile

---
name: systems/adios
description: >
  ADIOS2 I/O framework — stub for future use. Load this skill when the workflow reads
  or writes ADIOS2 BP files or uses the ADIOS2 Python bindings for high-performance I/O.
---

# ADIOS2 — System Skill (Stub)

ADIOS2 (Adaptable Input/Output System) provides high-performance I/O for scientific simulations, used to read/write large trajectory or field data files.

This skill is a placeholder. Populate with specifics when an ADIOS2 workflow is first implemented in MAW.

## Common Use Cases

- Reading large MD trajectory files in BP format
- Streaming simulation output in real-time (in-situ analysis)
- Paired with LAMMPS via `dump adios` in the LAMMPS input script

## Notes

- ADIOS2 Python bindings: `import adios2`
- Not currently in the MAW sandbox Dockerfile

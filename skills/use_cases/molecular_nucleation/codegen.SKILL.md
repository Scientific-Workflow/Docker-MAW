---
name: use_cases/molecular_nucleation/codegen
description: >
  Use-case-specific codegen rules for water crystallization via LAMMPS + OVITO + Parsl.
  Covers LAMMPS Python API, OVITO IdentifyDiamondModifier type mapping, visualization
  rules, argparse interface, file layout, and every hard-won fix from getting this workflow
  running. Load this whenever generating workflow code for the molecular nucleation project.
---

# Molecular Nucleation — Codegen Skill

Complete, authoritative rules for generating correct LAMMPS + OVITO workflow code. Every rule here was validated against a working run.

---

## When to Use This Skill

Load this whenever codegen is generating workflow.py for the molecular nucleation / water crystallization project. This skill overrides any vague or general guidance — follow these rules exactly.

Do NOT deviate from the rules below. They encode hard-won fixes for bugs that silently produce wrong results.

---

## Overview

The workflow: LAMMPS runs a water MD simulation → dumps atom trajectories → OVITO reads them and detects ice crystal structure → results saved to CSV → renders created.

**Input files (in /app/data/):** `in.watbox`, `data.init`, `AW.tersoff`
**Output files (in /app/work/run0/):** `frames/step.*.lammpstrj`, `results.csv`, render PNGs, animation GIF

---

## Step-by-Step Workflow

### Step 1: run_lammps @python_app

```python
@python_app
def run_lammps(input_script, data_dir, work_dir):
    import os, shutil
    # Parsl workers may not inherit LD_LIBRARY_PATH from the container ENV —
    # set it explicitly so the source-built liblammps.so is findable.
    os.environ['LD_LIBRARY_PATH'] = '/usr/local/lib'
    from lammps import lammps

    os.makedirs(work_dir, exist_ok=True)
    # Copy ALL files from data_dir — do not hardcode filenames
    for fname in os.listdir(data_dir):
        src = os.path.join(data_dir, fname)
        if os.path.isfile(src):
            shutil.copy2(src, work_dir)

    # CRITICAL: chdir into work_dir BEFORE running LAMMPS
    # in.watbox dumps to "frames/" relative to CWD — wrong CWD = frames go nowhere
    os.chdir(work_dir)
    os.makedirs("frames", exist_ok=True)

    lmp = lammps(cmdargs=["-screen", "none"])
    lmp.file(os.path.join(work_dir, os.path.basename(input_script)))
    lmp.close()
    return os.path.join(work_dir, "frames")
```

**Critical rules:**
- `os.environ['LD_LIBRARY_PATH'] = '/usr/local/lib'` BEFORE `from lammps import lammps` — Parsl workers don't reliably inherit container ENV vars; without this the import fails with `liblammps.so: cannot open shared object file`
- `os.chdir(work_dir)` BEFORE `lammps()` — this is mandatory
- Use `shutil.copy2` not `shutil.copy`
- NEVER modify the input script — do NOT change `run`, `timestep`, `variable T`, or any parameter
- `from lammps import lammps` — NEVER call lammps as a subprocess
- All imports inside the function body

### Step 2: analyze_with_ovito @python_app

```python
@python_app
def analyze_with_ovito(frames_dir, output_csv):
    import os, csv
    from ovito.io import import_file
    from ovito.modifiers import IdentifyDiamondModifier

    pipeline = import_file(os.path.join(frames_dir, "step.*.lammpstrj"))
    pipeline.modifiers.append(IdentifyDiamondModifier())

    with open(output_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["frame", "timestep", "cubic_diamond_count", "hexagonal_diamond_count"])
        for i in range(pipeline.source.num_frames):
            data = pipeline.compute(i)
            struct = data.particles["Structure Type"]
            # CRITICAL: count ALL subtypes for each crystal family
            cubic = int(((struct == 1) | (struct == 2) | (struct == 3)).sum())
            hexag = int(((struct == 4) | (struct == 5) | (struct == 6)).sum())
            writer.writerow([i, data.attributes.get("Timestep", i), cubic, hexag])

    return output_csv
```

**IdentifyDiamondModifier structure type mapping (CRITICAL — wrong mapping = zero counts in CSV):**
| Type | Meaning |
|---|---|
| 0 | Other (liquid, amorphous water) |
| 1 | Cubic diamond |
| 2 | Cubic diamond (1st neighbor) |
| 3 | Cubic diamond (2nd neighbor) |
| 4 | Hexagonal diamond (wurtzite ice) |
| 5 | Hexagonal diamond (1st neighbor) |
| 6 | Hexagonal diamond (2nd neighbor) |

Count cubic = types 1 + 2 + 3. Count hexagonal = types 4 + 5 + 6.
Counting only type 1 and type 3 misses most of the crystal atoms.

### Step 3: Visualization @python_app

```python
@python_app
def render_frames(frames_dir, work_dir):
    import os, glob
    import numpy as np
    from ovito.io import import_file
    from ovito.modifiers import IdentifyDiamondModifier
    from ovito.vis import Viewport
    import PIL.Image  # requires Pillow in Dockerfile

    pipeline = import_file(os.path.join(frames_dir, "step.*.lammpstrj"))
    pipeline.modifiers.append(IdentifyDiamondModifier())

    render_dir = os.path.join(work_dir, "renders")
    os.makedirs(render_dir, exist_ok=True)

    # Color mapping: type 0 = cyan (liquid), types 1-3 = blue (cubic), types 4-6 = red (hexagonal)
    # s=25 minimum — default s=2 makes liquid atoms invisible
    ...
```

**Visualization rules (MANDATORY — do not deviate):**
- Atom size: `s=25` MINIMUM — default s=2 makes liquid atoms invisible
- Liquid / Other (type 0): color `#00BFFF` (cyan), alpha ≥ 0.6
- Cubic diamond (types 1-3): color `#0000FF` (blue), alpha ≥ 0.6
- Hexagonal diamond (types 4-6): color `#FF2200` (red), alpha ≥ 0.6
- GIF generation: requires `Pillow` — use `PIL.Image.save(..., save_all=True, append_images=..., loop=0, duration=100)`

### Step 3b: render_nucleation_timeseries @python_app (REQUIRED — do not skip)

```python
@python_app
def render_nucleation_timeseries(csv_path, output_png):
    import os, csv as csv_mod
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    timesteps, cubic_counts, hex_counts = [], [], []
    with open(csv_path) as f:
        for row in csv_mod.DictReader(f):
            timesteps.append(int(float(row["timestep"])))
            cubic_counts.append(int(row["cubic_diamond_count"]))
            hex_counts.append(int(row["hexagonal_diamond_count"]))

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(timesteps, cubic_counts, color="#0000FF", marker="o", markersize=4, label="Cubic Diamond (Ice Ic)")
    ax.plot(timesteps, hex_counts,   color="#FF2200", marker="s", markersize=4, label="Hexagonal Diamond (Ice Ih)")
    total_ice = [c + h for c, h in zip(cubic_counts, hex_counts)]
    ax.plot(timesteps, total_ice, "k--", linewidth=1.5, alpha=0.6, label="Total Ice")
    ax.set_xlabel("Timestep"); ax.set_ylabel("Ice-like Atoms")
    ax.set_title("Water Freezing: Nucleation Progress"); ax.legend(); ax.grid(True, alpha=0.3)
    os.makedirs(os.path.dirname(output_png) or ".", exist_ok=True)
    fig.savefig(output_png, dpi=150, bbox_inches="tight"); plt.close(fig)
    return output_png
```

### Step 4: Argparse interface (EXACT — do not add or remove arguments)

```python
parser.add_argument("--data-dir", default="/app/data")
parser.add_argument("--work-dir", default="/app/work/run0")
```

Input script path is determined by the planner from the paper and the available data files list. The planner puts the exact filename in the tasks. Derive it in `main()` as `os.path.join(args.data_dir, "<filename from tasks>")`.
NO `--input-script` argument. The executor passes ONLY `--data-dir` and `--work-dir`. Any other argument causes "unrecognized arguments" crash.

### Step 5: main() — chain the apps

```python
def main():
    import argparse, os, parsl
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="/app/data")
    parser.add_argument("--work-dir", default="/app/work/run0")
    args = parser.parse_args()

    input_script = os.path.join(args.data_dir, "<input_script_filename>")  # use actual filename from planner tasks
    frames_dir   = run_lammps(input_script, args.data_dir, args.work_dir).result()
    output_csv   = analyze_with_ovito(frames_dir, os.path.join(args.work_dir, "results.csv")).result()
    render_frames(frames_dir, args.work_dir).result()
    render_nucleation_timeseries(output_csv, os.path.join(args.work_dir, "renders", "nucleation_timeseries.png")).result()
    print(f"Results: {output_csv}")
    parsl.clear()
```

---

## Input / Output

**Input data files:**
- `/app/data/in.watbox` — LAMMPS input script. Parameters: `run 9000`, `timestep 0.01`, `variable T equal 180`, `variable P equal 1.0`, `variable Nrestart equal 100`. DO NOT modify.
- `/app/data/data.init` — initial atom positions
- `/app/data/AW.tersoff` — Tersoff force field for water

**Output files (in `/app/work/run0/`):**
- `frames/step.*.lammpstrj` — trajectory dumps written by LAMMPS
- `results.csv` — columns: frame, timestep, cubic_diamond_count, hexagonal_diamond_count
- `renders/frame_*.png` — per-frame atom renders
- `renders/animation.gif` — animated GIF of the crystallization process

---

## Key Rules and Constraints

| Rule | Why |
|---|---|
| Never patch the `run` line in in.watbox | User controls simulation length; hardcoded override (e.g. 50000) ignores their config |
| Use `shutil.copy2` not `shutil.copy` | Preserves metadata; avoids subtle copy failures |
| `os.chdir(work_dir)` before lammps() | Dump path in in.watbox is relative — wrong CWD = no frames produced |
| `from lammps import lammps` | Source-built LAMMPS only works via Python API, not subprocess |
| Count types 1+2+3 and 4+5+6 separately | Counting only 1 and 3 gives ~10% of the actual crystal count |
| `s=25` minimum in visualization | Default atom size makes liquid phase invisible in renders |
| Pillow required for GIF | PIL.Image.save with save_all=True requires Pillow — it's in the Dockerfile |
| No `--input-script` arg | Executor hardcodes `--data-dir` and `--work-dir` only — extra arg crashes |

---

## Edge Cases

- **Stale frames from previous run:** If `work/run0/frames/` already has `.lammpstrj` files, OVITO reads those instead of running fresh. Codegen should not handle this — user must delete `work/` before re-running.
- **Old `image_tag` default:** `state.get("image_tag", "maw-sandbox:latest")` returns `""` when key exists. Use `state.get("image_tag") or "maw-sandbox:latest"`.

## Notes

- The Dockerfile uses source-built LAMMPS (no MPI) — `from lammps import lammps` works without any MPI setup
- Available packages are defined in `stack_decision` from state — do not assume a fixed package list. Check `stack_decision.pip_packages` and `stack_decision.special_installs` before importing anything.

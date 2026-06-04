#!/usr/bin/env python3
"""Parsl workflow: LAMMPS water crystallization simulation + OVITO diamond structure analysis."""

import argparse
import os
import sys

import parsl
from parsl.config import Config
from parsl.executors import HighThroughputExecutor
from parsl.providers import LocalProvider
from parsl.app.app import python_app

# ---------------------------------------------------------------------------
# Parsl configuration — single-node, no scheduler
# ---------------------------------------------------------------------------
config = Config(
    executors=[
        HighThroughputExecutor(
            label="local_htex",
            cores_per_worker=1,
            provider=LocalProvider(
                min_blocks=1,
                max_blocks=1,
                init_blocks=1,
            ),
        )
    ],
    strategy="none",
)

# ---------------------------------------------------------------------------
# App 1 – run LAMMPS
# ---------------------------------------------------------------------------
@python_app
def run_lammps(input_script, data_dir, work_dir):
    """Copy input files into *work_dir*, run LAMMPS, return path to frames/."""
    import os
    import shutil

    # Create work directory and frames sub-directory
    os.makedirs(work_dir, exist_ok=True)
    frames_dir = os.path.join(work_dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)

    # Copy required data files into work_dir
    for fname in ["data.init", "AW.tersoff"]:
        src = os.path.join(data_dir, fname)
        dst = os.path.join(work_dir, fname)
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)

    # Copy the input script into work_dir
    script_basename = os.path.basename(input_script)
    dst_script = os.path.join(work_dir, script_basename)
    if os.path.exists(input_script) and not os.path.exists(dst_script):
        shutil.copy2(input_script, dst_script)

    # CRITICAL: LAMMPS dumps to "frames/" relative to CWD
    os.chdir(work_dir)

    # Run LAMMPS via its Python interface
    from lammps import lammps  # noqa: E402

    lmp = lammps(cmdargs=["-screen", "none"])
    lmp.file(dst_script)
    lmp.close()

    return frames_dir


# ---------------------------------------------------------------------------
# App 2 – OVITO structural analysis
# ---------------------------------------------------------------------------
@python_app
def analyze_with_ovito(frames_dir, output_csv, output_png):
    """Load LAMMPS dumps, apply IdentifyDiamondModifier, write CSV & render PNG."""
    import os
    import glob
    import csv

    # Determine the glob pattern for dump files
    # The LAMMPS input writes:  dump … frames/step.*.lammpstrj
    pattern = os.path.join(frames_dir, "step.*.lammpstrj")
    files_found = sorted(glob.glob(pattern))
    if not files_found:
        # Fallback: try any lammpstrj
        pattern = os.path.join(frames_dir, "*.lammpstrj")
        files_found = sorted(glob.glob(pattern))
    if not files_found:
        raise FileNotFoundError(
            f"No LAMMPS dump files found in {frames_dir}"
        )

    # Use ovito to import
    from ovito.io import import_file
    from ovito.modifiers import IdentifyDiamondModifier

    # OVITO import_file: use a wildcard pattern so it loads ALL dump files
    # The pattern must use the directory + wildcard — not a single file.
    import re as _re
    # Build a pattern by replacing the numeric part with a wildcard
    first_file = files_found[0]
    fname_pattern = _re.sub(r'\d+\.lammpstrj$', '*.lammpstrj', first_file)
    pipeline = import_file(
        fname_pattern,
        sort_particles=True,
    )

    # Add diamond structure identification modifier
    pipeline.modifiers.append(IdentifyDiamondModifier())

    nframes = pipeline.source.num_frames
    if nframes == 0:
        raise RuntimeError("Pipeline loaded 0 frames.")

    # Ensure output directories exist
    os.makedirs(os.path.dirname(output_csv), exist_ok=True)
    os.makedirs(os.path.dirname(output_png), exist_ok=True)

    # Iterate over every frame, collect diamond structure counts
    rows = []
    for i in range(nframes):
        data = pipeline.compute(i)
        stypes = data.particles["Structure Type"].array
        timestep = data.attributes.get("Timestep", i)

        # IdentifyDiamondModifier structure types:
        #   0 = Other, 1 = Cubic diamond, 2 = Cubic diamond (1st neighbor),
        #   3 = Cubic diamond (2nd neighbor), 4 = Hexagonal diamond,
        #   5 = Hexagonal diamond (1st neighbor), 6 = Hexagonal diamond (2nd neighbor)
        import numpy as np
        cubic_count = int(np.isin(stypes, [1, 2, 3]).sum())
        hex_count = int(np.isin(stypes, [4, 5, 6]).sum())
        rows.append((i, int(timestep), cubic_count, hex_count))

    # Write CSV
    with open(output_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["frame", "timestep", "cubic_diamond_count", "hexagonal_diamond_count"]
        )
        writer.writerows(rows)

    # Render last frame with TachyonRenderer
    try:
        from ovito.vis import TachyonRenderer, Viewport
        import ovito

        # Make sure the pipeline visual element is added to scene
        pipeline.add_to_scene()

        # Compute last frame to set the scene state
        pipeline.compute(nframes - 1)

        vp = Viewport(type=Viewport.Type.Perspective)
        vp.zoom_all(size=(800, 600))

        renderer = TachyonRenderer(
            shadows=False,
            antialiasing=True,
            antialiasing_samples=2,
        )

        vp.render_image(
            filename=output_png,
            size=(800, 600),
            renderer=renderer,
            background=(1.0, 1.0, 1.0),
            alpha=False,
            frame=nframes - 1,
        )

        pipeline.remove_from_scene()
    except Exception as e:
        # Rendering is non-critical; log but don't fail
        print(f"[WARNING] Rendering skipped: {e}", flush=True)

    return output_csv


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Run LAMMPS water crystallization + OVITO analysis workflow"
    )
    parser.add_argument(
        "--data-dir",
        default="/app/data",
        help="Directory containing LAMMPS input files (in.watbox, data.init, AW.tersoff)",
    )
    parser.add_argument(
        "--work-dir",
        default="/app/work/run0",
        help="Working directory for the simulation run",
    )
    parser.add_argument(
        "--input-script",
        default="/app/data/in.watbox",
        help="Path to the LAMMPS input script",
    )
    args = parser.parse_args()

    data_dir = args.data_dir
    work_dir = args.work_dir
    input_script = args.input_script

    output_csv = os.path.join(work_dir, "diamond_structure.csv")
    output_png = os.path.join(work_dir, "diamond_structure.png")

    print("=" * 60)
    print("LAMMPS + OVITO Parsl Workflow")
    print("=" * 60)
    print(f"  Data directory   : {data_dir}")
    print(f"  Work directory   : {work_dir}")
    print(f"  Input script     : {input_script}")
    print(f"  Output CSV       : {output_csv}")
    print(f"  Output PNG       : {output_png}")
    print("=" * 60)

    # Load Parsl
    parsl.load(config)

    # --- Step 1: Run LAMMPS ---
    print("\n[STEP 1] Submitting LAMMPS simulation ...", flush=True)
    lammps_future = run_lammps(input_script, data_dir, work_dir)
    frames_dir = lammps_future.result()
    print(f"[STEP 1] LAMMPS complete. Frames at: {frames_dir}", flush=True)

    # List produced dump files
    import glob
    dump_files = sorted(glob.glob(os.path.join(frames_dir, "*.lammpstrj")))
    print(f"          Found {len(dump_files)} dump file(s).", flush=True)

    # --- Step 2: OVITO analysis ---
    print("\n[STEP 2] Submitting OVITO diamond structure analysis ...", flush=True)
    ovito_future = analyze_with_ovito(frames_dir, output_csv, output_png)
    csv_path = ovito_future.result()
    print(f"[STEP 2] OVITO analysis complete. CSV at: {csv_path}", flush=True)

    # --- Summary ---
    print("\n" + "=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    if os.path.exists(csv_path):
        with open(csv_path) as f:
            lines = f.readlines()
        print(f"  Total frames analysed: {len(lines) - 1}")
        # Print header + last few rows
        for line in lines[:1]:
            print(f"  {line.strip()}")
        for line in lines[-min(5, len(lines) - 1):]:
            print(f"  {line.strip()}")
    else:
        print("  [WARNING] CSV output not found.")

    if os.path.exists(output_png):
        print(f"\n  Rendered image saved to: {output_png}")
    else:
        print("\n  [INFO] No rendered image produced (rendering may not be supported).")

    print("\nWorkflow finished successfully.")

    parsl.clear()


if __name__ == "__main__":
    main()

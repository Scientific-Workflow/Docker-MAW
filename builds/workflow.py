#!/usr/bin/env python3
"""Parsl workflow: LAMMPS water freezing simulation + OVITO diamond structure analysis.

This workflow runs a LAMMPS MD simulation of water (using the provided input script
and force field files), then analyzes the trajectory with OVITO's
IdentifyDiamondModifier to detect cubic and hexagonal diamond (ice) structures.
Finally, it renders a visualization and produces a summary CSV.
"""

import argparse
import os
import sys

# ---------------------------------------------------------------------------
# Parsl configuration — local single-node executor (no SLURM, no MPI)
# ---------------------------------------------------------------------------
from parsl.config import Config
from parsl.executors import HighThroughputExecutor
from parsl.providers import LocalProvider
import parsl

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
# Parsl apps
# ---------------------------------------------------------------------------
from parsl import python_app


@python_app
def run_lammps(input_script, data_dir, work_dir):
    """Run LAMMPS inside *work_dir* using the Python wheel API.

    1. Copy data.init, AW.tersoff, and the input script into work_dir.
    2. cd into work_dir so that relative dump paths ("frames/...") resolve.
    3. Invoke LAMMPS via the Python bindings.
    4. Return the path to the frames/ directory.
    """
    import os
    import shutil
    import glob

    os.makedirs(work_dir, exist_ok=True)

    # Copy supporting data files
    for fname in ["data.init", "AW.tersoff"]:
        src = os.path.join(data_dir, fname)
        dst = os.path.join(work_dir, fname)
        if os.path.isfile(src):
            shutil.copy2(src, dst)

    # ALWAYS copy the input script fresh (user may have edited it)
    input_basename = os.path.basename(input_script)
    shutil.copy2(input_script, os.path.join(work_dir, input_basename))

    # Create frames/ subdirectory for trajectory dumps
    frames_dir = os.path.join(work_dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)

    # cd into work_dir so LAMMPS dump paths resolve correctly
    os.chdir(work_dir)

    # Run LAMMPS via the Python wheel API — DO NOT modify the input script
    from lammps import lammps
    lmp = lammps(cmdargs=["-screen", "none"])
    lmp.file(os.path.join(work_dir, input_basename))
    lmp.close()

    return frames_dir


@python_app
def analyze_with_ovito(frames_dir, output_csv):
    """Load LAMMPS dump files from *frames_dir*, apply IdentifyDiamondModifier,
    and write per-frame ice-structure counts to *output_csv*.

    Returns the path to the CSV.
    """
    import os
    import glob
    import csv

    # Discover dump files
    patterns = ["*.lammpstrj", "*.dump", "step.*", "*.lmp"]
    dump_files = []
    for pat in patterns:
        dump_files.extend(glob.glob(os.path.join(frames_dir, pat)))
    # Deduplicate
    dump_files = sorted(set(dump_files))

    if not dump_files:
        # Write empty CSV
        os.makedirs(os.path.dirname(output_csv), exist_ok=True)
        with open(output_csv, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["frame", "timestep", "cubic_diamond_count", "hexagonal_diamond_count"])
        return output_csv

    # Build a glob pattern for import_file
    # Try to identify the naming convention
    # Common: step.*.lammpstrj or dump.*.lammpstrj
    # We'll use the first file to figure out the pattern
    sample = os.path.basename(dump_files[0])
    # Try to construct a wildcard pattern
    # If files look like step.0.lammpstrj, step.1000.lammpstrj, etc.
    # Use a broad wildcard
    if "step." in sample:
        glob_pattern = os.path.join(frames_dir, "step.*")
    elif "dump." in sample:
        glob_pattern = os.path.join(frames_dir, "dump.*")
    else:
        # Fallback: use *.lammpstrj or just *
        lammpstrj = glob.glob(os.path.join(frames_dir, "*.lammpstrj"))
        if lammpstrj:
            glob_pattern = os.path.join(frames_dir, "*.lammpstrj")
        else:
            glob_pattern = os.path.join(frames_dir, "*")

    import ovito
    from ovito.io import import_file
    from ovito.modifiers import IdentifyDiamondModifier

    pipeline = import_file(glob_pattern, sort_particles=True)
    pipeline.modifiers.append(IdentifyDiamondModifier())

    num_frames = pipeline.source.num_frames
    if num_frames == 0:
        num_frames = 1  # at least try frame 0

    rows = []
    for frame_idx in range(num_frames):
        try:
            data = pipeline.compute(frame_idx)
        except Exception:
            continue

        # IdentifyDiamondModifier stores results in 'Structure Type' particle property
        struct_types = data.particles["Structure Type"]
        import numpy as np
        struct_array = np.array(struct_types)

        # Diamond structure types from OVITO:
        # 0 = Other
        # 1 = Cubic diamond
        # 2 = Cubic diamond (first neighbor)
        # 3 = Hexagonal diamond
        # 4 = Hexagonal diamond (first neighbor)
        cubic_diamond_count = int(np.isin(struct_array, [1, 2]).sum())
        hex_diamond_count = int(np.isin(struct_array, [3, 4]).sum())

        # Try to get timestep from attributes
        timestep = data.attributes.get("Timestep", frame_idx)

        rows.append([frame_idx, timestep, cubic_diamond_count, hex_diamond_count])

    os.makedirs(os.path.dirname(output_csv) if os.path.dirname(output_csv) else ".", exist_ok=True)
    with open(output_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["frame", "timestep", "cubic_diamond_count", "hexagonal_diamond_count"])
        writer.writerows(rows)

    return output_csv


@python_app
def render_visualization(frames_dir, output_png, csv_path):
    """Render a 3D scatter plot of the final frame, coloring atoms by
    diamond structure type.

    Colors:
      - liquid/other  : cyan   (#00BFFF)
      - cubic diamond : bright blue (#0000FF)
      - hex diamond   : bright red  (#FF2200)

    Returns the path to the saved PNG.
    """
    import os
    import glob
    import numpy as np

    # Discover dump files
    patterns = ["*.lammpstrj", "*.dump", "step.*", "*.lmp"]
    dump_files = []
    for pat in patterns:
        dump_files.extend(glob.glob(os.path.join(frames_dir, pat)))
    dump_files = sorted(set(dump_files))

    if not dump_files:
        # Create a placeholder image
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots()
        ax.text(0.5, 0.5, "No trajectory data", ha="center", va="center")
        os.makedirs(os.path.dirname(output_png) if os.path.dirname(output_png) else ".", exist_ok=True)
        fig.savefig(output_png, dpi=150)
        plt.close(fig)
        return output_png

    # Build glob pattern
    sample = os.path.basename(dump_files[0])
    if "step." in sample:
        glob_pattern = os.path.join(frames_dir, "step.*")
    elif "dump." in sample:
        glob_pattern = os.path.join(frames_dir, "dump.*")
    else:
        lammpstrj = glob.glob(os.path.join(frames_dir, "*.lammpstrj"))
        if lammpstrj:
            glob_pattern = os.path.join(frames_dir, "*.lammpstrj")
        else:
            glob_pattern = os.path.join(frames_dir, "*")

    import ovito
    from ovito.io import import_file
    from ovito.modifiers import IdentifyDiamondModifier

    pipeline = import_file(glob_pattern, sort_particles=True)
    pipeline.modifiers.append(IdentifyDiamondModifier())

    num_frames = pipeline.source.num_frames
    last_frame = max(0, num_frames - 1)
    data = pipeline.compute(last_frame)

    positions = np.array(data.particles["Position"])
    struct_types = np.array(data.particles["Structure Type"])

    # Color mapping
    # 0 = Other (liquid/amorphous) -> cyan
    # 1,2 = Cubic diamond -> blue
    # 3,4 = Hexagonal diamond -> red
    colors = np.zeros((len(struct_types), 4))  # RGBA
    # Default: cyan for other/liquid
    colors[:, :] = [0.0, 0.749, 1.0, 0.7]  # #00BFFF with alpha 0.7
    # Cubic diamond: bright blue
    cubic_mask = np.isin(struct_types, [1, 2])
    colors[cubic_mask] = [0.0, 0.0, 1.0, 0.85]  # #0000FF
    # Hexagonal diamond: bright red
    hex_mask = np.isin(struct_types, [3, 4])
    colors[hex_mask] = [1.0, 0.133, 0.0, 0.85]  # #FF2200

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d import Axes3D

    fig = plt.figure(figsize=(12, 10))
    ax = fig.add_subplot(111, projection="3d")

    # Subsample if too many atoms for plotting
    max_plot_atoms = 8000
    if len(positions) > max_plot_atoms:
        idx = np.random.choice(len(positions), max_plot_atoms, replace=False)
        positions_plot = positions[idx]
        colors_plot = colors[idx]
    else:
        positions_plot = positions
        colors_plot = colors

    ax.scatter(
        positions_plot[:, 0],
        positions_plot[:, 1],
        positions_plot[:, 2],
        c=colors_plot,
        s=30,
        alpha=0.7,
        edgecolors="none",
    )

    n_cubic = int(cubic_mask.sum())
    n_hex = int(hex_mask.sum())
    n_other = int((~cubic_mask & ~hex_mask).sum())
    total = len(struct_types)

    ax.set_title(
        f"Water Freezing — Diamond Structure Analysis (Frame {last_frame})\n"
        f"Cubic ice: {n_cubic}  |  Hex ice: {n_hex}  |  Liquid/other: {n_other}  |  Total: {total}",
        fontsize=11,
    )
    ax.set_xlabel("X (Å)")
    ax.set_ylabel("Y (Å)")
    ax.set_zlabel("Z (Å)")

    # Legend
    from matplotlib.lines import Line2D
    legend_elements = [
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#00BFFF",
               markersize=10, label=f"Liquid/Other ({n_other})"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#0000FF",
               markersize=10, label=f"Cubic Diamond ({n_cubic})"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#FF2200",
               markersize=10, label=f"Hexagonal Diamond ({n_hex})"),
    ]
    ax.legend(handles=legend_elements, loc="upper left", fontsize=9)

    os.makedirs(os.path.dirname(output_png) if os.path.dirname(output_png) else ".", exist_ok=True)
    fig.savefig(output_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    return output_png


@python_app
def render_nucleation_timeseries(csv_path, output_png):
    """Plot cubic and hexagonal diamond counts vs. timestep from CSV."""
    import os
    import csv as csv_mod
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    timesteps = []
    cubic_counts = []
    hex_counts = []

    with open(csv_path, "r") as f:
        reader = csv_mod.DictReader(f)
        for row in reader:
            timesteps.append(int(float(row["timestep"])))
            cubic_counts.append(int(row["cubic_diamond_count"]))
            hex_counts.append(int(row["hexagonal_diamond_count"]))

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(timesteps, cubic_counts, "b-o", markersize=4, label="Cubic Diamond (Ice Ic)", color="#0000FF")
    ax.plot(timesteps, hex_counts, "r-s", markersize=4, label="Hexagonal Diamond (Ice Ih)", color="#FF2200")

    if cubic_counts or hex_counts:
        total_ice = [c + h for c, h in zip(cubic_counts, hex_counts)]
        ax.plot(timesteps, total_ice, "k--", linewidth=1.5, alpha=0.6, label="Total Ice")

    ax.set_xlabel("Timestep", fontsize=12)
    ax.set_ylabel("Number of Ice-like Atoms", fontsize=12)
    ax.set_title("Water Freezing: Nucleation Progress\n(Diamond Structure Identification)", fontsize=13)
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)

    os.makedirs(os.path.dirname(output_png) if os.path.dirname(output_png) else ".", exist_ok=True)
    fig.savefig(output_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    return output_png


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="LAMMPS + OVITO water freezing workflow")
    parser.add_argument(
        "--data-dir",
        default="/app/data",
        help="Directory containing in.watbox, data.init, AW.tersoff",
    )
    parser.add_argument(
        "--work-dir",
        default="/app/work/run0",
        help="Output directory for frames, CSVs, renders",
    )
    args = parser.parse_args()

    data_dir = os.path.abspath(args.data_dir)
    work_dir = os.path.abspath(args.work_dir)
    input_script = os.path.join(data_dir, "in.watbox")

    print("=" * 70)
    print("Water Freezing MD Workflow")
    print("=" * 70)
    print(f"  Data directory : {data_dir}")
    print(f"  Work directory : {work_dir}")
    print(f"  Input script   : {input_script}")
    print()

    # Verify input files exist
    for fname in ["in.watbox", "data.init", "AW.tersoff"]:
        fpath = os.path.join(data_dir, fname)
        if not os.path.isfile(fpath):
            print(f"WARNING: Expected file not found: {fpath}")

    parsl.load(config)

    # --- Step 1: Run LAMMPS ---
    print("[1/4] Submitting LAMMPS simulation...")
    lammps_future = run_lammps(input_script, data_dir, work_dir)
    frames_dir = lammps_future.result()
    print(f"      LAMMPS complete. Frames directory: {frames_dir}")

    # Count dump files
    import glob
    dump_count = 0
    for pat in ["*.lammpstrj", "*.dump", "step.*"]:
        dump_count += len(glob.glob(os.path.join(frames_dir, pat)))
    print(f"      Found {dump_count} dump file(s) in frames/")

    # --- Step 2: Analyze with OVITO ---
    output_csv = os.path.join(work_dir, "diamond_analysis.csv")
    print("[2/4] Submitting OVITO diamond structure analysis...")
    ovito_future = analyze_with_ovito(frames_dir, output_csv)
    csv_path = ovito_future.result()
    print(f"      Analysis complete. CSV: {csv_path}")

    # --- Step 3: Render 3D visualization ---
    output_png = os.path.join(work_dir, "structure_visualization.png")
    print("[3/4] Rendering 3D structure visualization...")
    render_future = render_visualization(frames_dir, output_png, csv_path)
    png_path = render_future.result()
    print(f"      Visualization saved: {png_path}")

    # --- Step 4: Render time series plot ---
    timeseries_png = os.path.join(work_dir, "nucleation_timeseries.png")
    print("[4/4] Rendering nucleation time series plot...")
    ts_future = render_nucleation_timeseries(csv_path, timeseries_png)
    ts_path = ts_future.result()
    print(f"      Time series plot saved: {ts_path}")

    # --- Summary ---
    print()
    print("=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)

    import csv as csv_mod
    try:
        with open(csv_path, "r") as f:
            reader = csv_mod.DictReader(f)
            rows = list(reader)
        if rows:
            print(f"  Total frames analyzed: {len(rows)}")
            max_cubic = max(int(r["cubic_diamond_count"]) for r in rows)
            max_hex = max(int(r["hexagonal_diamond_count"]) for r in rows)
            print(f"  Max cubic diamond atoms in any frame : {max_cubic}")
            print(f"  Max hexagonal diamond atoms in any frame: {max_hex}")
            # Last frame summary
            last = rows[-1]
            print(f"  Last frame (frame {last['frame']}, timestep {last['timestep']}):")
            print(f"    Cubic diamond   : {last['cubic_diamond_count']}")
            print(f"    Hexagonal diamond: {last['hexagonal_diamond_count']}")

            # Check nucleation threshold (1/8 of total atoms)
            # We don't know total atoms from CSV, so just report
            total_ice_last = int(last["cubic_diamond_count"]) + int(last["hexagonal_diamond_count"])
            print(f"    Total ice-like  : {total_ice_last}")
        else:
            print("  No frames were analyzed (empty CSV).")
    except Exception as e:
        print(f"  Could not read CSV: {e}")

    print()
    print(f"  Output files:")
    print(f"    CSV           : {csv_path}")
    print(f"    3D render     : {png_path}")
    print(f"    Time series   : {ts_path}")
    print("=" * 70)
    print("Workflow complete!")

    parsl.clear()


if __name__ == "__main__":
    main()

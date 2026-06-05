#!/usr/bin/env python3
"""Parsl workflow for water crystallization simulation and analysis.

Stages:
  1. run_lammps        – execute LAMMPS MD simulation via Python API
  2. analyze_with_ovito – diamond structure identification on trajectory
  3. render_frames      – 3D scatter-plot visualizations + animated GIF
  4. plot_nucleation_timeseries – publication-quality time-series chart
"""

from parsl.config import Config
from parsl.executors import HighThroughputExecutor
from parsl.providers import LocalProvider
from parsl.app.app import python_app
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
parsl.load(config)


@python_app
def run_lammps(input_script, data_dir, work_dir):
    import os
    import shutil
    import glob

    os.makedirs(work_dir, exist_ok=True)

    # Copy ALL data files from data_dir into work_dir
    for fpath in glob.glob(os.path.join(data_dir, "*")):
        if os.path.isfile(fpath):
            shutil.copy2(fpath, work_dir)

    # Always copy the input script fresh (user may have edited it)
    shutil.copy2(input_script, work_dir)

    # CRITICAL: chdir into work_dir BEFORE running LAMMPS
    # Dump paths in in.watbox are relative to CWD
    os.chdir(work_dir)
    os.makedirs("frames", exist_ok=True)

    from lammps import lammps
    lmp = lammps(cmdargs=["-screen", "none"])
    lmp.file(os.path.basename(input_script))
    lmp.close()

    return os.path.join(work_dir, "frames")


@python_app
def analyze_with_ovito(frames_dir, output_csv, work_dir):
    import os
    import csv
    import numpy as np
    from ovito.io import import_file
    from ovito.modifiers import IdentifyDiamondModifier

    dump_pattern = os.path.join(frames_dir, "step.*.lammpstrj")
    pipeline = import_file(dump_pattern, sort_particles=True)
    pipeline.modifiers.append(IdentifyDiamondModifier())

    os.makedirs(os.path.dirname(output_csv) or ".", exist_ok=True)

    with open(output_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["frame", "timestep", "cubic_diamond_count", "hexagonal_diamond_count"])

        for i in range(pipeline.source.num_frames):
            data = pipeline.compute(i)
            struct = np.array(data.particles["Structure Type"])
            # Types 1,2,3 = cubic diamond family; types 4,5,6 = hexagonal diamond family
            cubic = int(np.isin(struct, [1, 2, 3]).sum())
            hexag = int(np.isin(struct, [4, 5, 6]).sum())
            timestep = data.attributes.get("Timestep", i)
            writer.writerow([i, timestep, cubic, hexag])

    return output_csv


@python_app
def render_frames(frames_dir, output_csv, renders_dir, work_dir):
    import os
    import csv as csv_mod
    import numpy as np
    from ovito.io import import_file
    from ovito.modifiers import IdentifyDiamondModifier
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d import Axes3D  # noqa: F401
    import PIL.Image

    os.makedirs(renders_dir, exist_ok=True)

    dump_pattern = os.path.join(frames_dir, "step.*.lammpstrj")
    pipeline = import_file(dump_pattern, sort_particles=True)
    pipeline.modifiers.append(IdentifyDiamondModifier())

    num_frames = pipeline.source.num_frames
    png_paths = []

    for i in range(num_frames):
        data = pipeline.compute(i)
        positions = np.array(data.particles["Position"])
        struct = np.array(data.particles["Structure Type"])
        timestep = data.attributes.get("Timestep", i)

        # Masks for each category
        mask_liquid = (struct == 0)
        mask_cubic = np.isin(struct, [1, 2, 3])
        mask_hex = np.isin(struct, [4, 5, 6])

        fig = plt.figure(figsize=(10, 8))
        ax = fig.add_subplot(111, projection="3d")

        # Plot liquid atoms (cyan)
        if mask_liquid.any():
            pts = positions[mask_liquid]
            ax.scatter(pts[:, 0], pts[:, 1], pts[:, 2],
                       c="#00BFFF", s=25, alpha=0.6, label="Liquid", edgecolors="none")

        # Plot cubic diamond atoms (blue)
        if mask_cubic.any():
            pts = positions[mask_cubic]
            ax.scatter(pts[:, 0], pts[:, 1], pts[:, 2],
                       c="#0000FF", s=25, alpha=0.6, label="Cubic Diamond", edgecolors="none")

        # Plot hexagonal diamond atoms (red)
        if mask_hex.any():
            pts = positions[mask_hex]
            ax.scatter(pts[:, 0], pts[:, 1], pts[:, 2],
                       c="#FF2200", s=25, alpha=0.6, label="Hex Diamond", edgecolors="none")

        ax.set_title(f"Frame {i} — Timestep {timestep}")
        ax.set_xlabel("X")
        ax.set_ylabel("Y")
        ax.set_zlabel("Z")
        ax.legend(loc="upper left", fontsize=8)

        png_path = os.path.join(renders_dir, f"frame_{i:04d}.png")
        fig.savefig(png_path, dpi=100, bbox_inches="tight")
        plt.close(fig)
        png_paths.append(png_path)

    # Assemble animated GIF
    if png_paths:
        images = [PIL.Image.open(p) for p in png_paths]
        gif_path = os.path.join(renders_dir, "animation.gif")
        images[0].save(
            gif_path,
            save_all=True,
            append_images=images[1:],
            duration=100,
            loop=0,
        )
    else:
        gif_path = ""

    return gif_path


@python_app
def plot_nucleation_timeseries(csv_path, output_png):
    import os
    import csv as csv_mod
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    timesteps = []
    cubic_counts = []
    hex_counts = []

    with open(csv_path) as f:
        for row in csv_mod.DictReader(f):
            timesteps.append(int(float(row["timestep"])))
            cubic_counts.append(int(row["cubic_diamond_count"]))
            hex_counts.append(int(row["hexagonal_diamond_count"]))

    total_ice = [c + h for c, h in zip(cubic_counts, hex_counts)]

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(timesteps, cubic_counts, color="#0000FF", marker="o", markersize=4, label="Cubic Ice (Ic)")
    ax.plot(timesteps, hex_counts, color="#FF2200", marker="s", markersize=4, label="Hexagonal Ice (Ih)")
    ax.plot(timesteps, total_ice, "k--", linewidth=1.5, alpha=0.6, label="Total Ice")
    ax.set_xlabel("Timestep")
    ax.set_ylabel("Number of Atoms")
    ax.set_title("Ice Nucleation Over Time")
    ax.legend()
    ax.grid(True, alpha=0.3)

    os.makedirs(os.path.dirname(output_png) or ".", exist_ok=True)
    fig.savefig(output_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    return output_png


def main():
    import argparse
    import os
    import glob

    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="/app/data")
    parser.add_argument("--work-dir", default="/app/work/run0")
    args = parser.parse_args()

    # Derive input script path by looking for in.* in data_dir
    candidates = glob.glob(os.path.join(args.data_dir, "in.*"))
    if not candidates:
        raise FileNotFoundError(f"No input script matching 'in.*' found in {args.data_dir}")
    input_script = candidates[0]
    print(f"Using input script: {input_script}")

    # Step 1: Run LAMMPS simulation
    print("=== Step 1: Running LAMMPS ===")
    frames_dir = run_lammps(input_script, args.data_dir, args.work_dir).result()
    print(f"Frames directory: {frames_dir}")

    # Step 2: Analyze with OVITO diamond structure identification
    print("=== Step 2: Analyzing with OVITO ===")
    output_csv = os.path.join(args.work_dir, "results.csv")
    csv_path = analyze_with_ovito(frames_dir, output_csv, args.work_dir).result()
    print(f"Analysis CSV: {csv_path}")

    # Step 3: Render 3D visualizations + GIF
    print("=== Step 3: Rendering frames ===")
    renders_dir = os.path.join(args.work_dir, "renders")
    gif_path = render_frames(frames_dir, csv_path, renders_dir, args.work_dir).result()
    print(f"Animation GIF: {gif_path}")

    # Step 4: Plot nucleation timeseries
    print("=== Step 4: Plotting nucleation timeseries ===")
    timeseries_png = os.path.join(args.work_dir, "renders", "nucleation_timeseries.png")
    ts_path = plot_nucleation_timeseries(csv_path, timeseries_png).result()
    print(f"Timeseries plot: {ts_path}")

    print(f"\nDone. Results at: {csv_path}")
    parsl.clear()


if __name__ == "__main__":
    main()

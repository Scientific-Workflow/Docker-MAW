#!/usr/bin/env python3
"""Parsl workflow for water crystallization simulation and analysis.

LAMMPS MD simulation -> OVITO diamond structure analysis -> visualization.
"""

from parsl.config import Config
from parsl.executors import HighThroughputExecutor
from parsl.providers import LocalProvider
from parsl.app.app import python_app
import parsl

config = Config(
    run_dir='/tmp/parsl_runinfo',
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
def run_lammps(lammps_input, data_dir, work_dir):
    import os
    import shutil

    # Parsl workers may not inherit LD_LIBRARY_PATH from the container ENV
    os.environ['LD_LIBRARY_PATH'] = '/usr/local/lib'

    from lammps import lammps

    os.makedirs(work_dir, exist_ok=True)

    # Copy all required data files into work_dir
    for fname in os.listdir(data_dir):
        src = os.path.join(data_dir, fname)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(work_dir, fname))

    # CRITICAL: chdir into work_dir BEFORE running LAMMPS
    # in.watbox dumps to "frames/" relative to CWD
    os.chdir(work_dir)
    os.makedirs("frames", exist_ok=True)

    lmp = lammps(cmdargs=["-screen", "none"])
    lmp.file(os.path.join(work_dir, lammps_input))
    lmp.close()

    return work_dir


@python_app
def analyze_ice(work_dir, output_csv):
    import os
    import csv
    import glob
    from ovito.io import import_file
    from ovito.modifiers import IdentifyDiamondModifier

    # Find trajectory files in the frames subdirectory
    frames_dir = os.path.join(work_dir, "frames")
    pattern = os.path.join(frames_dir, "step.*.lammpstrj")
    files = sorted(glob.glob(pattern))

    if not files:
        # Fallback: check for dump files directly in work_dir
        pattern = os.path.join(work_dir, "*.lammpstrj")
        files = sorted(glob.glob(pattern))

    if not files:
        raise FileNotFoundError(f"No lammpstrj files found in {frames_dir} or {work_dir}")

    # Use glob pattern for multi-file import
    pipeline = import_file(os.path.join(frames_dir, "step.*.lammpstrj"))
    pipeline.modifiers.append(IdentifyDiamondModifier())

    os.makedirs(os.path.dirname(output_csv) or ".", exist_ok=True)

    num_frames = pipeline.source.num_frames

    with open(output_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["frame", "timestep", "cubic_count", "hexagonal_count", "other_count", "total_atoms"])
        for i in range(num_frames):
            data = pipeline.compute(i)
            struct = data.particles["Structure Type"]
            # CRITICAL: count ALL subtypes for each crystal family
            # 0=Other, 1=Cubic, 2=Cubic 1st nbr, 3=Cubic 2nd nbr
            # 4=Hex, 5=Hex 1st nbr, 6=Hex 2nd nbr
            import numpy as np
            struct_array = np.array(struct)
            cubic = int(((struct_array == 1) | (struct_array == 2) | (struct_array == 3)).sum())
            hexag = int(((struct_array == 4) | (struct_array == 5) | (struct_array == 6)).sum())
            other = int((struct_array == 0).sum())
            total = len(struct_array)
            timestep = data.attributes.get("Timestep", i)
            writer.writerow([i, int(timestep), cubic, hexag, other, total])

    return output_csv


@python_app
def render_frame(work_dir, frame_index, output_image_path):
    import os
    import numpy as np
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d import Axes3D
    from ovito.io import import_file
    from ovito.modifiers import IdentifyDiamondModifier

    frames_dir = os.path.join(work_dir, "frames")
    pipeline = import_file(os.path.join(frames_dir, "step.*.lammpstrj"))
    pipeline.modifiers.append(IdentifyDiamondModifier())

    data = pipeline.compute(frame_index)
    positions = np.array(data.particles["Position"])
    struct = np.array(data.particles["Structure Type"])
    timestep = data.attributes.get("Timestep", frame_index)

    # Masks
    mask_other = (struct == 0)
    mask_cubic = ((struct == 1) | (struct == 2) | (struct == 3))
    mask_hex = ((struct == 4) | (struct == 5) | (struct == 6))

    fig = plt.figure(figsize=(10, 8))
    ax = fig.add_subplot(111, projection='3d')

    # Plot liquid/amorphous atoms (type 0) - cyan, small and transparent
    if mask_other.any():
        pos_other = positions[mask_other]
        # Subsample liquid atoms if too many (for performance)
        if len(pos_other) > 2000:
            idx = np.random.choice(len(pos_other), 2000, replace=False)
            pos_other = pos_other[idx]
        ax.scatter(pos_other[:, 0], pos_other[:, 1], pos_other[:, 2],
                   c='#00BFFF', s=25, alpha=0.15, label=f'Liquid ({mask_other.sum()})')

    # Plot cubic ice atoms (types 1-3) - blue
    if mask_cubic.any():
        pos_cubic = positions[mask_cubic]
        ax.scatter(pos_cubic[:, 0], pos_cubic[:, 1], pos_cubic[:, 2],
                   c='#0000FF', s=25, alpha=0.8, label=f'Cubic Ice ({mask_cubic.sum()})')

    # Plot hexagonal ice atoms (types 4-6) - red
    if mask_hex.any():
        pos_hex = positions[mask_hex]
        ax.scatter(pos_hex[:, 0], pos_hex[:, 1], pos_hex[:, 2],
                   c='#FF2200', s=25, alpha=0.8, label=f'Hex Ice ({mask_hex.sum()})')

    ax.set_xlabel('X (\u00c5)')
    ax.set_ylabel('Y (\u00c5)')
    ax.set_zlabel('Z (\u00c5)')
    ax.set_title(f'Frame {frame_index} (timestep {int(timestep)})')
    ax.legend(loc='upper left', fontsize=8)

    os.makedirs(os.path.dirname(output_image_path) or ".", exist_ok=True)
    fig.savefig(output_image_path, dpi=150, bbox_inches='tight')
    plt.close(fig)

    return output_image_path


@python_app
def generate_animation(image_dir, output_gif_path):
    import os
    import glob
    import re
    from PIL import Image

    pattern = os.path.join(image_dir, "frame_*.png")
    files = glob.glob(pattern)
    if not files:
        return output_gif_path

    # Numeric sort by frame number
    def frame_num(path):
        match = re.search(r'frame_(\d+)', os.path.basename(path))
        return int(match.group(1)) if match else 0

    files.sort(key=frame_num)

    images = [Image.open(f).convert('RGBA') for f in files]
    if len(images) == 0:
        return output_gif_path

    # Convert to RGB for GIF
    rgb_images = [img.convert('RGB') for img in images]

    os.makedirs(os.path.dirname(output_gif_path) or ".", exist_ok=True)
    rgb_images[0].save(
        output_gif_path,
        save_all=True,
        append_images=rgb_images[1:],
        duration=200,
        loop=0
    )

    return output_gif_path


@python_app
def plot_nucleation_timeseries(csv_path, output_plot_path):
    import os
    import csv as csv_mod
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    timesteps = []
    cubic_counts = []
    hex_counts = []
    other_counts = []

    with open(csv_path) as f:
        reader = csv_mod.DictReader(f)
        for row in reader:
            timesteps.append(int(float(row["timestep"])))
            cubic_counts.append(int(row["cubic_count"]))
            hex_counts.append(int(row["hexagonal_count"]))
            other_counts.append(int(row["other_count"]))

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(timesteps, cubic_counts, color='#0000FF', marker='o', markersize=4,
            label='Cubic Diamond (Ice Ic)')
    ax.plot(timesteps, hex_counts, color='#FF2200', marker='s', markersize=4,
            label='Hexagonal Diamond (Ice Ih)')
    total_ice = [c + h for c, h in zip(cubic_counts, hex_counts)]
    ax.plot(timesteps, total_ice, 'k--', linewidth=1.5, alpha=0.6, label='Total Ice')

    ax.set_xlabel('Timestep')
    ax.set_ylabel('Number of crystalline atoms')
    ax.set_title('Ice Nucleation Time Series')
    ax.legend()
    ax.grid(True, alpha=0.3)

    os.makedirs(os.path.dirname(output_plot_path) or ".", exist_ok=True)
    fig.savefig(output_plot_path, dpi=150, bbox_inches='tight')
    plt.close(fig)

    return output_plot_path


def main():
    import argparse
    import os

    parser = argparse.ArgumentParser(description='Water crystallization workflow')
    parser.add_argument('--data-dir', default='/app/data')
    parser.add_argument('--work-dir', default='/app/work/run0')
    args = parser.parse_args()

    output_dir = os.path.join(args.work_dir, "output")
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(args.work_dir, exist_ok=True)

    # Step 1: Run LAMMPS simulation
    print("=== Step 1: Running LAMMPS simulation ===")
    sim_result = run_lammps(
        lammps_input='in.watbox',
        data_dir=args.data_dir,
        work_dir=args.work_dir
    ).result()
    print(f"LAMMPS simulation complete. Work dir: {sim_result}")

    # Step 2: Analyze ice structure with OVITO
    print("=== Step 2: Analyzing ice structure ===")
    csv_path = os.path.join(output_dir, 'ice_counts.csv')
    csv_result = analyze_ice(
        work_dir=sim_result,
        output_csv=csv_path
    ).result()
    print(f"Analysis complete. CSV: {csv_result}")

    # Step 3: Determine number of frames and render a subset
    print("=== Step 3: Rendering frames ===")
    # Read the CSV to find how many frames we have
    import csv as csv_mod
    frame_count = 0
    with open(csv_result) as f:
        reader = csv_mod.DictReader(f)
        for row in reader:
            frame_count += 1

    # Select frames to render: every Nth frame, up to ~50 frames max
    if frame_count <= 50:
        frames_to_render = list(range(frame_count))
    else:
        step = max(1, frame_count // 50)
        frames_to_render = list(range(0, frame_count, step))
        # Always include the last frame
        if frames_to_render[-1] != frame_count - 1:
            frames_to_render.append(frame_count - 1)

    print(f"Total frames: {frame_count}, rendering {len(frames_to_render)} frames")

    render_futures = []
    for fi in frames_to_render:
        out_img = os.path.join(output_dir, f"frame_{fi:05d}.png")
        fut = render_frame(
            work_dir=sim_result,
            frame_index=fi,
            output_image_path=out_img
        )
        render_futures.append(fut)

    # Collect all render results
    rendered_paths = [f.result() for f in render_futures]
    print(f"Rendered {len(rendered_paths)} frames")

    # Step 4: Generate animation GIF
    print("=== Step 4: Generating animation ===")
    gif_path = os.path.join(output_dir, 'nucleation.gif')
    anim_result = generate_animation(
        image_dir=output_dir,
        output_gif_path=gif_path
    ).result()
    print(f"Animation: {anim_result}")

    # Step 5: Plot nucleation timeseries
    print("=== Step 5: Plotting nucleation timeseries ===")
    plot_path = os.path.join(output_dir, 'nucleation_timeseries.png')
    plot_result = plot_nucleation_timeseries(
        csv_path=csv_result,
        output_plot_path=plot_path
    ).result()
    print(f"Timeseries plot: {plot_result}")

    print("\n=== Workflow complete ===")
    print(f"  CSV results:      {csv_result}")
    print(f"  Animation:        {anim_result}")
    print(f"  Timeseries plot:  {plot_result}")
    print(f"  Rendered frames:  {output_dir}/frame_*.png")

    parsl.clear()


if __name__ == '__main__':
    main()

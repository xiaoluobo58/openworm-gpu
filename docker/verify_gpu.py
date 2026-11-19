"""Run the Windows OpenWorm GPU acceptance checks and CPU/GPU benchmark."""

import argparse
import json
import math
from pathlib import Path
import re
from statistics import median
import subprocess
import sys
from time import perf_counter
from typing import NamedTuple


ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
CONFIGURATION = "worm_crawl_half_resolution"
CONTAINER_ROOT = "/home/ow/shared"
SMOKE_MS = 15
BENCHMARK_MS = 100
REPEATS = 3
RMS_LIMIT = 0.01


class Run(NamedTuple):
    folder: Path
    solver_seconds: float
    wall_seconds: float


def command(args, expected_failure=None):
    print(f"Running {subprocess.list2cmdline(args)}", flush=True)
    start = perf_counter()
    with subprocess.Popen(
        args,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    ) as process:
        lines = []
        for line in process.stdout:
            print(line, end="", flush=True)
            lines.append(line)
        status = process.wait()
    elapsed = perf_counter() - start
    output = "".join(lines)
    if expected_failure is not None:
        if status != 1 or expected_failure not in output:
            raise RuntimeError("Removing GPU passthrough did not trigger the GPU preflight error")
    elif status:
        raise RuntimeError(f"Command returned exit code {status}")
    return output, elapsed


def simulation(mode, duration, device_name, cold=False):
    image = f"openworm-{mode.lower()}:{VERSION}"
    if cold:
        # Without the named cache volume, this container compiles the kernels once.
        args = [
            "docker", "run", "--rm", "--init", "--gpus", "all",
            "--env", f"OW_OUT_DIR={CONTAINER_ROOT}", "--env", f"DURATION={duration}",
            "--mount", f"type=bind,source={ROOT},target={CONTAINER_ROOT}", image,
        ]
    else:
        args = ["cmd.exe", "/d", "/c", "run.cmd", f"-{mode.lower()}", "-d", str(duration)]
    output, elapsed = command(args)
    selected = re.search(r"CL_DEVICE_TYPE\s*\[\d+\]:\s*(CPU|GPU)\b", output)
    name = re.search(r"CL_DEVICE_NAME\s*\[\d+\]:[^\S\n]*(.+)", output)
    if not selected or selected[1] != mode or not name:
        raise RuntimeError(f"Solver did not report an actual {mode} device")
    if mode == "GPU" and device_name.casefold() not in name[1].casefold():
        raise RuntimeError(f"Solver selected {name[1].strip()}, expected {device_name}")

    created = re.search(r"^Created new output directory: .*/output/(C2_FW_[\w-]+)$", output, re.M)
    if not created:
        raise RuntimeError("The simulation did not publish a new c302 output directory")
    folder = ROOT / "output" / created[1]
    report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    if (
        report["completion_status"] != "Completed successfully"
        or report["device"] != mode
        or report["configuration"] != CONFIGURATION
        or report["noc302"] is not False
        or float(report["duration"].split()[0]) != duration
    ):
        raise RuntimeError(f"Unexpected simulation settings or result in {folder / 'report.json'}")
    solver_seconds = float(report["run_time"].split()[0])
    if not math.isfinite(solver_seconds) or solver_seconds <= 0:
        raise RuntimeError(f"Invalid solver timing in {folder / 'report.json'}")
    return Run(folder, solver_seconds, elapsed)


def verify_movie(run, mode):
    if not any(figure.stat().st_size for figure in run.folder.glob("*.png")):
        raise RuntimeError(f"No figures in {run.folder}")
    movie = f"/run-output/{run.folder.name}.mp4"
    output, _ = command([
        "docker", "run", "--rm", "--mount",
        f"type=bind,source={run.folder},target=/run-output,readonly",
        f"openworm-{mode.lower()}:{VERSION}", "ffmpeg", "-v", "error", "-xerror",
        "-progress", "pipe:1", "-nostats", "-i", movie, "-map", "0:v:0", "-f", "null", "-",
    ])
    if not any(int(frame) > 0 for frame in re.findall(r"^frame=\s*(\d+)", output, re.M)):
        raise RuntimeError(f"No decodable video frames in {run.folder}")


def trajectory(folder):
    path = folder / "worm_motion_log.txt"
    frames = []
    with path.open(encoding="utf-8") as motion:
        for line in motion:
            timestamp, x, y, z = re.split(r"\s+[XYZ]:\s+", line.strip())
            time = float(timestamp)
            coordinates = [tuple(map(float, axis.split())) for axis in (x, y, z)]
            if (
                not math.isfinite(time)
                or (frames and time <= frames[-1][0])
                or {len(axis) for axis in coordinates} != {99}
                or any(not math.isfinite(value) for axis in coordinates for value in axis)
            ):
                raise RuntimeError(f"Invalid trajectory sample in {path}")
            frames.append((time, tuple(zip(*coordinates))))
    if len(frames) < 2:
        raise RuntimeError(f"Too few trajectory samples in {path}")
    return frames


def relative_rms(cpu, gpu):
    if [time for time, _ in cpu] != [time for time, _ in gpu]:
        raise RuntimeError("CPU and GPU trajectory sample times differ")

    # Sibernetic writes zero-filled slots where there is no centerline particle.
    indices = [i for i, point in enumerate(cpu[0][1]) if any(point)]
    if indices != [i for i, point in enumerate(gpu[0][1]) if any(point)]:
        raise RuntimeError("CPU and GPU recorded different centerline points")
    centerline = [cpu[0][1][i] for i in indices]
    body_length = sum(math.dist(a, b) for a, b in zip(centerline, centerline[1:]))
    if not math.isfinite(body_length) or body_length <= 0:
        raise RuntimeError("Initial worm body length is invalid")

    squared_error = sum(
        math.dist(cpu_points[i], gpu_points[i]) ** 2
        for (_, cpu_points), (_, gpu_points) in zip(cpu, gpu)
        for i in indices
    )
    return math.sqrt(squared_error / (len(cpu) * len(indices))) / body_length


def verify(device_name, smoke_only):
    command(["cmd.exe", "/d", "/c", "run.cmd", "-check-gpu"])
    command(
        ["docker", "run", "--rm", f"openworm-gpu:{VERSION}", "python3", "openworm_gpu.py"],
        expected_failure="OpenWorm GPU preflight failed:",
    )
    cpu = simulation("CPU", SMOKE_MS, device_name)
    gpu = simulation("GPU", SMOKE_MS, device_name)
    verify_movie(cpu, "CPU")
    verify_movie(gpu, "GPU")
    error = relative_rms(trajectory(cpu.folder), trajectory(gpu.folder))
    print(f"{SMOKE_MS} ms centerline RMS deviation is {error:.3%} of initial body length.")
    if error > RMS_LIMIT:
        raise RuntimeError(f"Trajectory RMS deviation exceeds {RMS_LIMIT:.0%}")
    if smoke_only:
        return

    cold = simulation("GPU", SMOKE_MS, device_name, cold=True)
    warm = simulation("GPU", SMOKE_MS, device_name)
    print(
        f"GPU cache-free and warm solver times were {cold.solver_seconds:.2f} s and "
        f"{warm.solver_seconds:.2f} s. Estimated cache-free overhead was "
        f"{cold.solver_seconds - warm.solver_seconds:.2f} s."
    )

    samples = {"CPU": [], "GPU": []}
    for repeat in range(REPEATS):
        for mode in samples:
            print(f"{mode} {BENCHMARK_MS} ms trial {repeat + 1}/{REPEATS}", flush=True)
            samples[mode].append(simulation(mode, BENCHMARK_MS, device_name))

    cpu_solver = median(run.solver_seconds for run in samples["CPU"])
    gpu_solver = median(run.solver_seconds for run in samples["GPU"])
    cpu_wall = median(run.wall_seconds for run in samples["CPU"])
    gpu_wall = median(run.wall_seconds for run in samples["GPU"])
    print(
        f"Median solver time was {cpu_solver:.2f} s on CPU and {gpu_solver:.2f} s on GPU "
        f"({cpu_solver / gpu_solver:.2f}x speedup)."
    )
    print(
        f"Median wall time was {cpu_wall:.2f} s on CPU and {gpu_wall:.2f} s on GPU "
        f"({cpu_wall - gpu_wall:+.2f} s saved)."
    )
    if gpu_solver >= cpu_solver:
        raise RuntimeError("GPU median solver time did not beat the CPU median")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device-name", default="RTX 2080", help="Expected GPU name substring")
    parser.add_argument("--smoke-only", action="store_true", help="Skip the timing benchmark")
    args = parser.parse_args()
    try:
        verify(args.device_name, args.smoke_only)
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        sys.exit(f"OpenWorm GPU verification failed: {error}")


if __name__ == "__main__":
    main()

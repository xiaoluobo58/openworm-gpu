import atexit
import json
import math
import os
import re
import shlex
import shutil
import sys
import tempfile
import time
from pathlib import Path
from subprocess import PIPE, STDOUT, Popen, TimeoutExpired, run
from uuid import uuid4

import matplotlib

matplotlib.use("Agg")

print("*****************************")
print("  OpenWorm Master Script")
print("*****************************")
print("")
print("This script attempts to run a full pass through the OpenWorm software stack.")
print(
    "This depends on several other repositories being loaded to work and presumes it is running in a preloaded Docker instance."
)
print("It will report where steps are missing.")
print("Eventually all the steps will be filled in.")
print("")


print("****************************")
print("  Step 1: Run c302 + Sibernetic in the same loop.")
print("****************************")

OW_OUT_DIR = os.environ["OW_OUT_DIR"]


def execute_with_realtime_output(
    command, directory, env=None, exit_on_failure=True, expected_device=None
):
    p = None
    selected_device = None
    try:
        print(">>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>")
        print(">> Executing command: %s" % command)
        print(">> --------------------------------------------------------------")
        args = shlex.split(command) if isinstance(command, str) else command
        p = Popen(args, stdout=PIPE, stderr=STDOUT, cwd=directory, env=env)
        with p.stdout:
            for line in iter(p.stdout.readline, b""):
                message = line.decode("utf-8", errors="replace")
                print(">>  %s" % message, end="", flush=True)
                if expected_device:
                    match = re.search(r"CL_DEVICE_TYPE\s*\[\d+\]:\s*(\w+)", message)
                    if match:
                        selected_device = match.group(1)
        p.wait()  # wait for the subprocess to exit
    except KeyboardInterrupt as e:
        print("Caught CTRL+C")
        if p:
            p.kill()
            p.wait()
        raise e
    except Exception as e:
        print(">>  Caught an exception...")
        print(">>  %s" % e)
    print(">> --------------------------------------------------------------")
    print(
        ">> Command exited with %s: %s"
        % (p.returncode if p is not None else "-none-", command)
    )
    print(">>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>\n")

    if p is None or p.returncode != 0:
        print("Exiting master Python script as the last command failed")
        if exit_on_failure:
            sys.exit(p.returncode if p and p.returncode else 111)
        return False

    if expected_device and selected_device != expected_device:
        print(f"Sibernetic did not select the requested {expected_device} device.")
        return False

    return True


device = os.environ.get("OW_DEVICE", "CPU").upper()
if device not in {"CPU", "GPU"}:
    sys.exit("OW_DEVICE must be CPU or GPU")
if device == "GPU":
    from openworm_gpu import require_gpu

    require_gpu()

sys.path.append(os.environ["C302_HOME"])

try:
    if os.access(OW_OUT_DIR, os.W_OK) is not True:
        os.system(
            "sudo chown -R %s:%s %s"
            % (os.environ["USER"], os.environ["USER"], OW_OUT_DIR)
        )
except Exception:
    print("Unexpected error: %s" % sys.exc_info()[0])
    raise

# Default is 15 ms of simulation time.
try:
    sim_duration = float(os.environ.get("DURATION", "15"))
except ValueError:
    sys.exit("DURATION must be a positive number in milliseconds")
if not math.isfinite(sim_duration) or sim_duration <= 0:
    sys.exit("DURATION must be a positive number in milliseconds")


configuration = "worm_crawl_half_resolution"
if "CONFIGURATION" in os.environ:
    configuration = os.environ["CONFIGURATION"]

if "NOC302" in os.environ:
    noc302 = os.environ["NOC302"] == "1"
else:
    noc302 = "worm" not in configuration

PARAMETERS = {
    "duration": sim_duration,
    "dt": 0.005,
    "dtNrn": 0.05,
    "logstep": 100,
    "reference": "FW",  # "TargetMuscle",
    "c302params": "C2",
    "verbose": False,
    "device": device,
    "configuration": configuration,
    "noc302": noc302,
    "datareader": "UpdatedSpreadsheetDataReader2",
    "outDir": OW_OUT_DIR,
}

my_env = os.environ.copy()
DISPLAY = ":44"
my_env["DISPLAY"] = DISPLAY

# Sibernetic playback is rendered into this virtual X11 screen.
xvfb = Popen(["Xvfb", DISPLAY, "-nolisten", "tcp", "-screen", "0", "1920x1080x24+32"])


def stop_xvfb():
    if xvfb.poll() is None:
        xvfb.terminate()
        try:
            xvfb.wait(timeout=3)
        except TimeoutExpired:
            xvfb.kill()
            xvfb.wait()


atexit.register(stop_xvfb)

sim_prefix = "Sibernetic" if noc302 else "%s_%s" % (PARAMETERS["c302params"], PARAMETERS["reference"])
run_tag = uuid4().hex[:8]
sim_name = "%s_%s_%s" % (sim_prefix, time.strftime("%Y-%m-%d_%H-%M-%S"), run_tag)
sibernetic_home = Path(os.environ["SIBERNETIC_HOME"])
simulations = sibernetic_home / "simulations"
# Legacy CPU images pin a wrapper that predates -simName and -q.
modern_wrapper = Path(__file__).with_name("openworm_gpu.py").is_file()
wrapper_options = ["-outDir", "simulations", "-simName", sim_name, "-q"]
if not modern_wrapper:
    simulations.mkdir(exist_ok=True)
    legacy_run_dir = simulations / run_tag
    wrapper_options = ["-outDir", str(legacy_run_dir.relative_to(sibernetic_home))]

try:
    print("Starting Sibernetic simulation with parameters:")
    for p in PARAMETERS:
        print("  %s: %s" % (p, PARAMETERS[p]))

    command = """python3 sibernetic_c302.py
                -duration %s
                -dt %s
                -dtNrn %s
                -logstep %s
                -device=%s
                -configuration %s
                -reference %s
                -c302params %s
                -datareader %s""" % (
        PARAMETERS["duration"],
        PARAMETERS["dt"],
        PARAMETERS["dtNrn"],
        PARAMETERS["logstep"],
        PARAMETERS["device"],
        shlex.quote(PARAMETERS["configuration"]),
        PARAMETERS["reference"],
        PARAMETERS["c302params"],
        PARAMETERS["datareader"],
    )
    command = shlex.split(command) + wrapper_options

    if noc302:
        command.append("-noc302")

    success = execute_with_realtime_output(
        command,
        sibernetic_home,
        env=my_env,
        exit_on_failure=False,
        expected_device="GPU" if device == "GPU" else None,
    )

except KeyboardInterrupt:
    sys.exit(130)

report_path = simulations / sim_name / "report.json"
if not modern_wrapper:
    reports = list(legacy_run_dir.glob("*/report.json"))
    if len(reports) == 1:
        report_path = reports[0]
try:
    with open(report_path, encoding="utf-8") as report_file:
        report = json.load(report_file)
except (OSError, ValueError) as error:
    sys.exit(f"Sibernetic did not produce a usable report for {sim_name}: {error}")
if not isinstance(report, dict):
    sys.exit(f"Sibernetic produced an invalid report for {sim_name}")
if report.get("completion_status") != "Completed successfully":
    sys.exit(f"Sibernetic failed: {report.get('completion_status', 'missing completion status')}")
if not success:
    sys.exit(f"Sibernetic failed to finish using the requested {device} device")

latest_subdir = report_path.parent
print("\n========================================================================\n")
print("Finished main simulation, data saved in: %s" % latest_subdir)
output_dir = Path(OW_OUT_DIR) / "output"
output_dir.mkdir(exist_ok=True)
new_sim_out = output_dir / (sim_name if modern_wrapper else f"{latest_subdir.name}_{run_tag}")
new_sim_out.mkdir()
print(f"Created new output directory: {new_sim_out}", flush=True)

wcons = sorted(latest_subdir.glob("*.wcon"))
for source in [
    *sorted(latest_subdir.glob("*.png")),
    *sorted(latest_subdir.glob("report*")),
    *wcons,
]:
    print(f"Moving {source} to {new_sim_out}")
    shutil.move(source, new_sim_out)

movie = new_sim_out / f"{new_sim_out.name}.mp4"
recorder = Popen(
    [
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
        "-f", "x11grab", "-framerate", "30", "-draw_mouse", "0",
        "-video_size", "1920x1080", "-i", DISPLAY, "-vf", "crop=1200:800:100:100",
        "-b:v", "384k", "-qmin", "10", "-qmax", "42", "-maxrate", "384k",
        "-bufsize", "1000k", "-an", str(movie),
    ],
    stdin=PIPE,
    env=my_env,
)
try:
    # Give the recorder time to attach before starting playback.
    time.sleep(3)
    if recorder.poll() is not None:
        sys.exit(f"FFmpeg could not start recording on {DISPLAY}")
    execute_with_realtime_output(
        ["./Release/Sibernetic", "-f", configuration, "-l_from", f"lpath={latest_subdir}"],
        sibernetic_home,
        env=my_env,
    )
finally:
    try:
        recorder.communicate(b"q\n" if recorder.poll() is None else None, timeout=15)
    except TimeoutExpired:
        recorder.kill()
        recorder.communicate()
    for source in [*sorted(latest_subdir.glob("*.txt")), *sorted(latest_subdir.glob("*.dat"))]:
        print(f"Moving {source} to {new_sim_out}")
        shutil.move(source, new_sim_out)

if recorder.returncode != 0 or not movie.is_file() or not movie.stat().st_size:
    sys.exit(f"FFmpeg could not finalize {movie}")

# Keep the original recording and generate the optional trimmed and sped-up versions.
detection = run(
    [
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(movie), "-vf",
        "blackdetect=d=0:pic_th=0.70:pix_th=0.10", "-an", "-f", "null", "-",
    ],
    capture_output=True,
    text=True,
)
if detection.returncode:
    sys.exit(f"FFmpeg could not decode {movie}: {detection.stderr.strip()}")

black = re.search(r"black_start:(\S+)\s+black_end:(\S+)", detection.stderr)
if black and float(black.group(1)) == 0 and float(black.group(2)) > 0:
    trimmed_movie = movie.with_name(f"cut_{movie.name}")
    trimmed = execute_with_realtime_output(
        [
            "ffmpeg", "-y", "-ss", black.group(2), "-i", str(movie),
            "-c", "copy", "-avoid_negative_ts", "1", str(trimmed_movie),
        ],
        sibernetic_home,
        exit_on_failure=False,
    )
    if trimmed:
        with tempfile.TemporaryDirectory(prefix="openworm-frames-") as frames_dir:
            frames = str(Path(frames_dir) / "out%06d.jpg")
            extracted = execute_with_realtime_output(
                ["ffmpeg", "-ss", "1", "-i", str(trimmed_movie), "-vf", "fps=1", frames],
                sibernetic_home,
                exit_on_failure=False,
            )
            if extracted and any(Path(frames_dir).glob("*.jpg")):
                execute_with_realtime_output(
                    [
                        "ffmpeg", "-y", "-r", "100", "-i", frames, "-r", "100",
                        "-vb", "60M", str(movie.with_name(f"speeded_{movie.name}")),
                    ],
                    sibernetic_home,
                    exit_on_failure=False,
                )

print("****************************")
print("  Step 2: Run movement analysis")
print("****************************")
print("Not yet implemented.")
print(
    "Note however the following WCON files have been generated into %s during the simulation: %s"
    % (new_sim_out, [w.name for w in wcons])
)

print("****************************")
print(" Step 3: Report on movement analysis fit to real worm videos")
print("****************************")
print("Not yet implemented.")

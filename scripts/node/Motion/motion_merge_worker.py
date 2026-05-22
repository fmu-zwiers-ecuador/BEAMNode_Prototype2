#!/usr/bin/env python3
"""
motion_merge_worker.py

Worker for combining motion video and audio clips.

It can run one job with --job or run continuously with --watch. The continuous
mode is intended for a separate systemd service so final-video processing is
independent from beam_motion_trigger.py.
"""

import argparse
import fcntl
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path


LOG_DIR = Path("/home/pi/logs")
LOCK_PATH = LOG_DIR / "motion_merge.lock"
VIDEO_PROCESSING_LOG_PATH = LOG_DIR / "motion_video_processing.log"
DEFAULT_QUEUE_DIR = Path("/home/pi/data/motion_events")


def append_merge_log(log_path, message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write(f"{timestamp} {message}\n")
    except Exception:
        pass


def load_job(job_path):
    with open(job_path, "r") as f:
        return json.load(f)


def claim_job(job_path):
    if job_path.name.endswith(".running.json"):
        return job_path

    running_path = job_path.with_name(job_path.name.replace(".merge.json", ".merge.running.json"))
    try:
        job_path.rename(running_path)
    except FileNotFoundError:
        return None
    except Exception:
        return None
    return running_path


def finish_job(job_path, succeeded):
    if not job_path.exists():
        return

    suffix = ".merge.done.json" if succeeded else ".merge.failed.json"
    if job_path.name.endswith(".merge.running.json"):
        finished_path = job_path.with_name(job_path.name.replace(".merge.running.json", suffix))
    elif job_path.name.endswith(".merge.json"):
        finished_path = job_path.with_name(job_path.name.replace(".merge.json", suffix))
    else:
        finished_path = job_path.with_suffix(job_path.suffix + (".done" if succeeded else ".failed"))

    try:
        job_path.rename(finished_path)
    except Exception:
        pass


def build_merge_command(job, output_path=None):
    duration_sec = float(job["duration_sec"])
    audio_trim_start_sec = float(job["audio_trim_start_sec"])
    video_fps = float(job["video_fps"])
    final_output = output_path if output_path is not None else job["final_output"]

    if audio_trim_start_sec >= 0:
        audio_filter = (
            f"[1:a]asetpts=PTS-STARTPTS,"
            f"atrim=start={audio_trim_start_sec}:duration={duration_sec},"
            f"asetpts=PTS-STARTPTS,"
            f"apad=pad_dur={duration_sec},"
            f"atrim=duration={duration_sec},asetpts=PTS-STARTPTS[a]"
        )
    else:
        delay_ms = int(round(abs(audio_trim_start_sec) * 1000))
        audio_filter = (
            f"[1:a]asetpts=PTS-STARTPTS,"
            f"adelay={delay_ms}:all=1,"
            f"apad=pad_dur={duration_sec},"
            f"atrim=duration={duration_sec},asetpts=PTS-STARTPTS[a]"
        )

    return [
        "ffmpeg", "-y",
        "-i", str(job["video_file"]),
        "-i", str(job["audio_file"]),
        "-filter_complex",
        (
            f"[0:v]setpts=PTS-STARTPTS,"
            f"tpad=stop_mode=clone:stop_duration={duration_sec},"
            f"trim=duration={duration_sec},setpts=PTS-STARTPTS[v];"
            f"{audio_filter}"
        ),
        "-map", "[v]",
        "-map", "[a]",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-r", f"{video_fps:g}",
        "-c:a", "aac",
        "-t", str(duration_sec),
        str(final_output),
    ]


def run_merge(job_path, claim=True):
    recovering_interrupted_job = job_path.name.endswith(".merge.running.json")
    claimed_job_path = claim_job(job_path) if claim else job_path
    if claimed_job_path is None:
        return 0

    try:
        job = load_job(claimed_job_path)
    except Exception:
        finish_job(claimed_job_path, False)
        return 1

    merge_log_path = Path(job.get("merge_log_path", str(VIDEO_PROCESSING_LOG_PATH)))
    final_output = Path(job["final_output"])
    temp_output = final_output.with_suffix(final_output.suffix + ".tmp")

    if recovering_interrupted_job:
        append_merge_log(merge_log_path, f"RECOVERING_INTERRUPTED_JOB job={claimed_job_path}")

    append_merge_log(merge_log_path, f"WORKER_STARTED job={claimed_job_path} pid={os.getpid()}")

    if final_output.exists() and final_output.stat().st_size > 0:
        append_merge_log(merge_log_path, f"SKIPPED output_already_exists={final_output}")
        finish_job(claimed_job_path, True)
        return 0

    missing_inputs = [
        path for path in [Path(job["video_file"]), Path(job["audio_file"])]
        if not path.exists()
    ]
    if missing_inputs:
        append_merge_log(merge_log_path, f"FAILED missing_inputs={missing_inputs}")
        finish_job(claimed_job_path, False)
        return 1

    final_output.parent.mkdir(parents=True, exist_ok=True)
    try:
        temp_output.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    try:
        os.nice(10)
    except Exception:
        pass

    with open(LOCK_PATH, "w") as lock_file:
        append_merge_log(merge_log_path, f"WAITING_FOR_LOCK lock={LOCK_PATH}")
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        append_merge_log(merge_log_path, "LOCK_ACQUIRED")

        cmd = build_merge_command(job, temp_output)
        append_merge_log(
            merge_log_path,
            (
                f"START video={job['video_file']} audio={job['audio_file']} "
                f"output={job['final_output']} temp_output={temp_output} "
                f"duration={float(job['duration_sec']):.3f}s "
                f"fps={float(job['video_fps']):g} "
                f"audio_trim_start={float(job['audio_trim_start_sec']):.3f}s"
            ),
        )

        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
        except Exception as e:
            append_merge_log(merge_log_path, f"FAILED_TO_START {e}")
            finish_job(claimed_job_path, False)
            return 1

        if result.returncode == 0 and temp_output.exists():
            temp_output.replace(final_output)
            append_merge_log(merge_log_path, f"COMPLETE output={final_output}")
            finish_job(claimed_job_path, True)
            return 0

        append_merge_log(merge_log_path, f"FAILED returncode={result.returncode}")
        if result.stderr:
            append_merge_log(merge_log_path, f"FFMPEG_STDERR {result.stderr.strip()}")
        try:
            temp_output.unlink()
        except Exception:
            pass
        finish_job(claimed_job_path, False)
        return result.returncode or 1


def find_pending_jobs(queue_dir):
    if not queue_dir.exists():
        return []
    pending = list(queue_dir.rglob("*.merge.json"))
    interrupted = list(queue_dir.rglob("*.merge.running.json"))
    return sorted(pending + interrupted)


def watch_queue(queue_dir, poll_sec):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    append_merge_log(VIDEO_PROCESSING_LOG_PATH, f"WATCH_STARTED queue={queue_dir} pid={os.getpid()}")

    while True:
        pending_jobs = find_pending_jobs(queue_dir)
        if not pending_jobs:
            time.sleep(poll_sec)
            continue

        for job_path in pending_jobs:
            run_merge(job_path, claim=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", default=None)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--queue-dir", default=str(DEFAULT_QUEUE_DIR))
    parser.add_argument("--poll-sec", type=float, default=5.0)
    args = parser.parse_args()

    if args.watch:
        watch_queue(Path(args.queue_dir), args.poll_sec)
        return

    if args.job is None:
        parser.error("--job is required unless --watch is used")

    raise SystemExit(run_merge(Path(args.job), claim=True))


if __name__ == "__main__":
    main()

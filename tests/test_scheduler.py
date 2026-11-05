import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest.mock import call, patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEDULER_PATH = REPO_ROOT / "scripts" / "node" / "scheduler.py"


def load_scheduler_module():
    spec = importlib.util.spec_from_file_location("beam_scheduler", SCHEDULER_PATH)
    module = importlib.util.module_from_spec(spec)
    with patch("os.makedirs"):
        spec.loader.exec_module(module)
    return module


scheduler = load_scheduler_module()


class SchedulerTimeoutTests(unittest.TestCase):
    def test_non_audio_defaults_to_30_seconds(self):
        self.assertEqual(scheduler.get_sensor_timeout("bme280", {}), 30)

    def test_audio_timeout_is_duration_plus_30(self):
        self.assertEqual(
            scheduler.get_sensor_timeout("audio", {"duration_sec": 300}),
            330,
        )

    def test_explicit_timeout_overrides_audio_duration(self):
        self.assertEqual(
            scheduler.get_sensor_timeout(
                "audio",
                {"duration_sec": 300, "timeout_sec": 45},
            ),
            45,
        )

    def test_invalid_timeout_logs_warning_and_uses_default(self):
        with patch.object(scheduler, "log") as log_mock:
            timeout_sec = scheduler.get_sensor_timeout(
                "audio",
                {"duration_sec": 300, "timeout_sec": 0},
            )

        self.assertEqual(timeout_sec, 330)
        log_mock.assert_called_once()
        self.assertIn("Invalid timeout_sec", log_mock.call_args.args[0])

    def test_timed_out_sensor_is_logged_and_later_sensor_runs(self):
        timeout = subprocess.TimeoutExpired(["python3", "/tmp/slow.py"], 1)
        completed = subprocess.CompletedProcess(
            ["python3", "/tmp/fast.py"],
            returncode=0,
            stdout="",
            stderr="",
        )

        with (
            patch.object(
                scheduler,
                "find_sensor_script",
                side_effect=["/tmp/slow.py", "/tmp/fast.py"],
            ),
            patch.object(
                scheduler.subprocess,
                "run",
                side_effect=[timeout, completed],
            ) as run_mock,
            patch.object(scheduler, "log") as log_mock,
        ):
            scheduler.run_sensor_once("slow", {"timeout_sec": 1})
            scheduler.run_sensor_once("fast", {})

        self.assertEqual(run_mock.call_count, 2)
        self.assertEqual(
            run_mock.call_args_list,
            [
                call(
                    ["python3", "/tmp/slow.py"],
                    capture_output=True,
                    text=True,
                    timeout=1,
                ),
                call(
                    ["python3", "/tmp/fast.py"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                ),
            ],
        )
        self.assertTrue(
            any(
                "slow timed out after 1 seconds" in logged.args[0]
                for logged in log_mock.call_args_list
            )
        )
        self.assertTrue(
            any(
                "fast finished successfully" in logged.args[0]
                for logged in log_mock.call_args_list
            )
        )


if __name__ == "__main__":
    unittest.main()

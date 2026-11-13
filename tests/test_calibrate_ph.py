"""The pH wizard must only calibrate a pH circuit, using the pH buffer points."""

import builtins
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
CALIBRATE_PH_PATH = REPO_ROOT / "scripts" / "node" / "atlas_sci" / "calibrate_ph.py"


def load_wizard():
    # fcntl only exists on Linux/macOS; the wizard needs it only for real I2C.
    with patch.dict(sys.modules, {"fcntl": types.SimpleNamespace(ioctl=lambda *a: None)}):
        spec = importlib.util.spec_from_file_location("calibrate_ph", CALIBRATE_PH_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class FakeCircuit:
    """Answers the EZO commands the wizard sends and records them."""

    def __init__(self, identity="?I,pH,2.16", rejected=(), reported_points=None):
        self.identity = identity
        self.rejected = rejected
        self.reported_points = reported_points
        self.points = 0
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def query(self, command, timeout=0, num_bytes=31):
        self.sent.append(command)
        if command in self.rejected:
            return 2, "", [2]
        if command == "I":
            return 1, self.identity, [1]
        if command == "Cal,?":
            points = self.points if self.reported_points is None else self.reported_points
            return 1, f"?Cal,{points}", [1]
        if command == "Cal,mid,7.00":
            self.points = 1
        elif command.startswith("Cal,"):
            self.points += 1
        elif command == "R":
            return 1, "7.012", [1]
        return 1, "", [1]

    @property
    def calibrations(self):
        return [c for c in self.sent if c.startswith("Cal,") and c != "Cal,?"]


class CalibratePhTests(unittest.TestCase):
    def setUp(self):
        self.wizard = load_wizard()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config_path = Path(tmp.name) / "config.json"
        self.config_path.write_text(
            json.dumps(
                {
                    "atlas_ph": {"enabled": False, "i2c_bus": 1, "address_hex": "0x63"},
                    "atlas_do": {"enabled": False, "address_hex": "0x61"},
                }
            )
        )
        self.wizard.CONFIG_PATH = str(self.config_path)
        self.wizard.countdown = lambda seconds: None

    def run_wizard(self, circuit, answers):
        opened = []

        def open_circuit(address, bus=1):
            opened.append((address, bus))
            return circuit

        self.wizard.AtlasI2CDevice = open_circuit
        replies = iter(answers)
        with patch.object(builtins, "input", lambda *args: next(replies)), patch(
            "sys.stdout"
        ), patch("sys.stderr"):
            self.wizard.main()
        self.assertIn(opened, ([], [(0x63, 1)]))

    def run_wizard_expecting_exit(self, circuit, answers):
        with self.assertRaises(SystemExit):
            self.run_wizard(circuit, answers)

    def config(self):
        return json.loads(self.config_path.read_text())

    def test_three_point_calibration(self):
        circuit = FakeCircuit()
        self.run_wizard(circuit, ["", "", "", ""])

        self.assertEqual(
            circuit.calibrations, ["Cal,mid,7.00", "Cal,low,4.00", "Cal,high,10.00"]
        )
        self.assertEqual(circuit.sent[-2], "Cal,?")
        self.assertIs(self.config()["atlas_ph"]["enabled"], True)
        self.assertEqual(self.config()["atlas_do"], {"enabled": False, "address_hex": "0x61"})

    def test_low_and_high_points_are_optional(self):
        circuit = FakeCircuit()
        self.run_wizard(circuit, ["", "", "skip", "skip"])

        self.assertEqual(circuit.calibrations, ["Cal,mid,7.00"])
        self.assertIs(self.config()["atlas_ph"]["enabled"], True)

    def test_other_circuits_are_refused(self):
        for identity in ("?I,DO,2.16", "?I,ORP,2.0", "?I,EC,2.1"):
            with self.subTest(identity=identity):
                circuit = FakeCircuit(identity=identity)
                self.run_wizard_expecting_exit(circuit, [""])

                self.assertEqual(circuit.calibrations, [])
                self.assertIs(self.config()["atlas_ph"]["enabled"], False)

    def test_failed_mid_point_stops_the_wizard(self):
        circuit = FakeCircuit(rejected=("Cal,mid,7.00",))
        self.run_wizard_expecting_exit(circuit, ["", ""])

        self.assertEqual(circuit.calibrations, ["Cal,mid,7.00"])
        self.assertIs(self.config()["atlas_ph"]["enabled"], False)

    def test_rejected_optional_point_stops_the_wizard(self):
        cases = [
            ("Cal,low,4.00", ["", "", "", "skip"], ["Cal,mid,7.00", "Cal,low,4.00"]),
            (
                "Cal,high,10.00",
                ["", "", "", ""],
                ["Cal,mid,7.00", "Cal,low,4.00", "Cal,high,10.00"],
            ),
            ("Cal,high,10.00", ["", "", "skip", ""], ["Cal,mid,7.00", "Cal,high,10.00"]),
        ]
        for rejected, answers, sent in cases:
            with self.subTest(rejected=rejected, answers=answers):
                circuit = FakeCircuit(rejected=(rejected,))
                self.run_wizard_expecting_exit(circuit, answers)

                self.assertEqual(circuit.calibrations, sent)
                self.assertIs(self.config()["atlas_ph"]["enabled"], False)

    def test_point_count_mismatch_does_not_enable(self):
        circuit = FakeCircuit(reported_points=2)
        self.run_wizard_expecting_exit(circuit, ["", "", "skip", "skip"])

        self.assertIs(self.config()["atlas_ph"]["enabled"], False)

    def test_targets_ph_section_and_address_only(self):
        source = CALIBRATE_PH_PATH.read_text(encoding="utf-8")

        self.assertIn('"atlas_ph"', source)
        self.assertIn('"0x63"', source)
        self.assertNotIn("atlas_do", source)
        self.assertNotIn("0x61", source)
        self.assertNotIn("dissolved oxygen", source.lower())


if __name__ == "__main__":
    unittest.main()

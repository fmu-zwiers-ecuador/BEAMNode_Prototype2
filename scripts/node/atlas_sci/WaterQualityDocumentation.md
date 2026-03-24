# **Atlas EZO Sensor Documentation:**

### **Basic Info**

* **Task Title:** Atlas EZO Sensor Scripts (ORP / EC / RTD)
* **Student(s):** Jackson Roberts
* **Mentor/Reviewer:** Raiz Mohammed
* **Date Started / Completed:** 03/11/2026 - 03/24/2026
* **Status:** Done
* **GitHub Link (examples):**
  - https://github.com/fmu-zwiers-ecuador/BEAMNode_Prototype2/blob/main/scripts/node/atlas_sci/log_atlas_orp.py
  - https://github.com/fmu-zwiers-ecuador/BEAMNode_Prototype2/blob/main/scripts/node/atlas_sci/calibrate_orp.py
  - https://github.com/fmu-zwiers-ecuador/BEAMNode_Prototype2/blob/main/scripts/node/atlas_sci/log_atlas_ec.py
  - https://github.com/fmu-zwiers-ecuador/BEAMNode_Prototype2/blob/main/scripts/node/atlas_sci/calibrate_ec.py

---

### **1) Summary**

This document describes the set of helper scripts used to interact with Atlas Scientific EZO circuits (ORP, EC, RTD) from the BEAMNode prototype. The scripts provide:

- Low-level I2C access wrappers compatible with the InterLink Pi HAT.
- Logging scripts that read sensor values and append them to JSON files.
- Interactive calibration wizards for EC and ORP.
- Utilities to switch Atlas EZO circuits from UART to I2C.

Each script follows a config-driven approach reading `scripts/node/config.json` for bus and address settings so the same code runs across multiple nodes.

### **2) Goals**

* **Main Goal:** Provide robust, reproducible scripts to read and calibrate Atlas EZO sensors and log results to persistent JSON files.
* **Success Metric:** Scripts run on the Pi without I2C errors, calibration commands persist on the EZO circuits, and data files (`orp_data.json`, `ec_data.json`, `water_temp.json`) update as expected.

### **3) Setup**

* **Hardware:** Raspberry Pi (or Pi Zero with InterLink HAT), Atlas EZO ORP/EC/RTD circuits seated on the HAT, Atlas probe(s).
* **Software:** Python 3.x, `smbus2` (if using smbus variant), or direct `/dev/i2c-*` access via `io` + `fcntl` as implemented in the scripts.

Install/Run Steps:

```bash
# (on the Pi) - ensure I2C enabled in raspi-config
sudo apt update && sudo apt install -y i2c-tools python3-pip
pip3 install smbus2

# verify device present (expect 0x62 for ORP by default)
sudo i2cdetect -y 1

# Run the ORP calibration wizard (interactive)
sudo python3 /home/pi/BEAMNode_Prototype2/scripts/node/atlas_sci/calibrate_orp.py

# Run the ORP logger once
sudo python3 /home/pi/BEAMNode_Prototype2/scripts/node/atlas_sci/log_atlas_orp.py

# Run the EC calibration wizard
sudo python3 /home/pi/BEAMNode_Prototype2/scripts/node/atlas_sci/calibrate_ec.py

# Switch ORP from UART to I2C (if required)
sudo python3 /home/pi/BEAMNode_Prototype2/scripts/node/atlas_sci/switch_orp_to_i2c.py
```

### **4) Method**

1. **Config-driven behavior**: All scripts read `scripts/node/config.json` for `atlas_orp`, `atlas_ec`, and `atlas_rtd` sections. This controls `i2c_bus`, `address_hex`, output directory and filenames.
2. **I2C access**: The code provides two patterns depending on script: a raw `/dev/i2c-*` `AtlasI2CDevice` implementation (used by logging and our calibrator) which avoids the overhead of smbus on constrained Pi Zeros, and an `smbus2`-based helper used by EC calibration because of convenient read/write helpers. Both patterns send text commands terminated with CR (0x0D) and parse status + ASCII payload responses per Atlas EZO protocol.
3. **Calibration flow**: The calibrators follow Atlas-recommended steps: identity check (`I`), query calibration status (`Cal,?`), optional `Cal,clear`, then single-point `Cal,225` (ORP) or EC flows (`Cal,one/low/high`) with generous delays to allow the InterLink HAT to bridge I2C.
4. **Safe file writes**: Logging scripts use atomic writes (write to temp file then os.replace) to avoid JSON corruption on power loss.

### **5) Code**

* **Main Scripts:**

- `log_atlas_orp.py` — reads ORP and appends `orp_data.json`.
- `calibrate_orp.py` — interactive ORP calibration wizard (single-point 225 mV).
- `switch_orp_to_i2c.py` — helper to switch a UART-mode ORP circuit to I2C address 0x62.
- `log_atlas_ec.py` — reads EC probe and appends `ec_data.json`.
- `calibrate_ec.py` — interactive EC calibration wizard (supports K constant, dry, single/two-point flow).

**Example: run ORP logger**

```bash
sudo python3 /home/pi/BEAMNode_Prototype2/scripts/node/atlas_sci/log_atlas_orp.py
```

**Data Structure Example (ORP)**

```json
{
  "node_id": "beam-node-01",
  "sensor": "atlas_orp",
  "records": [
    {
      "timestamp": "2026-03-24T12:00:00Z",
      "local_timestamp": "2026-03-24T12:00:00+00:00",
      "orp_mV": 223.6
    }
  ]
}
```

### **6) Testing**

* **I2C detection:** `sudo i2cdetect -y 1` should show the expected device address (0x62 ORP, 0x64 EC default, 0x66 RTD default).  If not present, confirm HAT seating and run `switch_*_to_i2c.py` if the EZO is in UART mode.
* **Calibration script tests:** Run `calibrate_orp.py` and `calibrate_ec.py` interactively. Confirm `Cal,?` reports stored calibration (`,1` or `,2`) after completion.
* **Logger test:** Run `log_atlas_orp.py` and check `/home/pi/data/orp/orp_data.json` for appended records and correct timestamp format.

### **7) Lessons & Next Steps**

* **What worked well:** Reusing the `AtlasI2CDevice` raw interface ensures consistent response parsing across EZO circuits and keeps timing predictable with HAT bridging.
* **Problems faced:** InterLink/hat bridging increases required delays; scripts therefore use conservative timeouts. Unexpectedly long `STILL_PROCESSING` responses may need a larger timeout on some older Pi Zero models.
* **Suggestions:**
  - Add a `--noninteractive` flag to calibration scripts for automated mass-deployment.
  - Add a `check_orp_cal.py` style quick diagnostic (like EC's `check_ec_cal.py`) for faster health checks.

### **8) References**

* Atlas Scientific EZO Protocol docs — command and response formats
* i2c-tools (`i2cdetect`) — for I2C bus debugging

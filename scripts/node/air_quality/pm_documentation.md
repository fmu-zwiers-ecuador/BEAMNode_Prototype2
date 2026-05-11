# **PMSA003I Sensor Documentation:** 

### **Basic Info**

* **Task Title:** PMSA003I Air Quality Sensor Setup  
* **Student(s):** Jackson Roberts  
* **Mentor/Reviewer:** Raiz Mohammed  
* **Date Started / Completed:** 2/25/2026-5/11/2026  
* **Status:** Done  
* **GitHub Link:** [https://github.com/fmu-zwiers-ecuador/BEAMNode_Prototype2/main/scripts/node/air_quality/log_pm_data.py]

---

### **1) Summary**

I developed a Python script to interface with the **PMSA003I particulate matter sensor** via I2C. This script reads 12 distinct particulate matter measurements (PM1.0, PM2.5, PM10 in two modes, and particle counts by size), validates PMS frame integrity via checksums, and appends the data to a persistent JSON log file. The implementation includes robust error handling, frame validation, and support for I2C bus fallback mechanisms.

### **2) Goals**

* **Main Goal:** Successfully read 12-parameter air quality data from PMSA003I sensor and store it in structured JSON format.  
* **Success Metric:** Script executes without I2C bus errors, validates frame checksums, and creates/updates `pm_data.json` with valid timestamps and particle measurements.

### **3) Setup**

* **Hardware:** Raspberry Pi, PMSA003I Sensor (I2C mode, address 0x12).  
* **Software:** Python 3.x, `smbus2` library.

**Install/Run Steps:**  
```bash
pip3 install smbus2

python3 log_pm_data.py
```

**Configuration (config.json):**
```json
{
  "air_quality": {
    "enabled": true,
    "interface": "i2c",
    "i2c_bus": 1,
    "i2c_address": "0x12",
    "i2c_poll_interval_sec": 0.2,
    "read_timeout_sec": 3.0,
    "directory": "air_quality",
    "file_name": "pm_data.json",
    "i2c_candidates": [
      {"bus": 1, "address": "0x12"}
    ]
  }
}
```

### **4) Method**

1. **Config-Driven Logic:** Used a central `config.json` to define the I2C bus (`1` by default), sensor address (`0x12`), and output directories. Supports multiple I2C bus candidates for fallback.  
2. **Frame Validation:** Implemented strict PMS frame validation checking:
   - Correct header bytes (`0x42`, `0x4D`)
   - Frame length verification (28 bytes of data)
   - CRC16 checksum validation (last 2 bytes)
3. **Robust I2C Handling:** Built a candidate-based retry mechanism that polls multiple I2C targets within a configurable timeout window (default 3 seconds).  
4. **Fallback Strategy:** When valid frames are received but contain all-zero values, the script logs a warning and records the latest valid frame to prevent data loss.  
5. **Persistent Storage:** Created a robust file handler that reads existing logs, appends new data points with UTC/local timestamps, and rewrites the file atomically.

### **5) Code**

* **Main Script:** `log_pm_data.py`  
* **Example Run:** `python3 log_pm_data.py`

**Data Structure Example:**

```json
{
    "node_id": "pi-01",
    "sensor": "air_quality",
    "records": [
        {
            "timestamp_utc": "2026-05-11T14:23:45.123456+00:00",
            "local_time": "2026-05-11 14:23:45",
            "timezone": "UTC",
            "pm1_0_cf1_ug_m3": 8,
            "pm2_5_cf1_ug_m3": 14,
            "pm10_cf1_ug_m3": 24,
            "pm1_0_atm_ug_m3": 7,
            "pm2_5_atm_ug_m3": 12,
            "pm10_atm_ug_m3": 20,
            "particles_0_3um_per_0_1L": 1250,
            "particles_0_5um_per_0_1L": 450,
            "particles_1_0um_per_0_1L": 180,
            "particles_2_5um_per_0_1L": 42,
            "particles_5_0um_per_0_1L": 8,
            "particles_10um_per_0_1L": 2
        }
    ]
}
```

**Key Data Fields:**
- **PM measurements (CF=1 mode):** Factory calibration conditions
- **PM measurements (ATM mode):** Standard atmospheric conditions  
- **Particle counts:** Particles per 0.1L of air, grouped by size ranges (0.3µm to 10µm)

### **6) Testing**

* **Manual I2C Check:** Verified I2C connectivity with `i2cdetect -y 1` to confirm PMSA003I at address `0x12`.  
* **Frame Validation Test:** Ran the script and captured multiple frames to verify checksum validation and payload parsing.  
* **Fallback Test:** Unplugged I2C cable and verified timeout handling; confirmed script retries candidates and provides helpful error messages.  
* **Zero-Value Handling:** Monitored for zero-valued frames (common during sensor startup) and verified warning logging behavior.  
* **JSON Integrity:** Confirmed generated JSON file matched expected schema with all 12 measurements present.  
* **Result:** **Pass.** Frames validated correctly, data appended without corruption, and error handling provided clear feedback.

### **7) Lessons & Next Steps**

* **What worked well:** 
  - The CRC16 checksum validation reliably filtered corrupted frames.
  - The I2C candidate list supports multiple bus configurations for hardware flexibility.
  - The polling loop gracefully handles timeouts and provides detailed diagnostics.

* **Problems faced:** 
  - Initial zero-valued frames during sensor warm-up; resolved by implementing fallback and warning mechanism.
  - Occasional invalid frames on the I2C bus; the validation layer successfully filters these.

* **Suggestions:** 
  - For next phase, consider implementing rate limiting to prevent excessive I2C traffic.
  - Add automatic sensor warm-up detection to skip initial zero-valued readings.
  - Implement atomic file writes (temp file + rename) to prevent corruption during power loss, similar to BME680 recommendations.

### **8) References**

* [Plantower PMSA003I Datasheet](https://cdn.shopify.com/s/files/1/0176/3274/files/PMSA003I_Series_Data_Sheet_V2.4_EN.pdf)
* [I2C Communication Overview](../docs/)
* [SMBus2 Python Library Documentation](https://github.com/nfviso/smbus2)

# **BME680 Sensor Documentation:** 

### **Basic Info**

* **Task Title:** BME680 Sensor Setup  
* **Student(s):** Jackson Roberts  
* **Mentor/Reviewer:** Raiz Mohammed  
* **Date Started / Completed:** 2/23/2025-2/25/2026  
* **Status:** Done  
* **GitHub Link:** [https://github.com/fmu-zwiers-ecuador/BEAMNode\_Prototype2/blob/main/scripts/node/bme680/log\_env680\_data.py](https://github.com/fmu-zwiers-ecuador/BEAMNode_Prototype2/blob/main/scripts/node/bme680/log_env680_data.py) 

---

### **1\) Summary**

I developed a Python script to interface with the **BME680 environmental sensor** via I2C. This script reads temperature, humidity, pressure, and gas resistance, then appends the data to a persistent JSON log file. It ensures data integrity by dynamically loading configurations and handling sensor "burn-in" for the gas VOC readings.

### **2\) Goals**

* **Main Goal:** Successfully read 4-point environmental data and store it in a structured JSON format.  
* **Success Metric:** Script executes without I2C bus errors and creates/updates `bme680_env.json` with valid timestamps.

### **3\) Setup**

* **Hardware:** Raspberry Pi, BME680 Sensor (I2C mode).  
* **Software:** Python 3.x, `adafruit-circuitpython-bme680`, `board`.

**Install/Run Steps:**  
Bash  
pip3 install adafruit-circuitpython-bme680

python3 bme680\_logger.py

* 

### **4\) Method**

1. **Config-Driven Logic:** Used a central `config.json` to define the I2C address (`0x77` or `0x76`) and output directories.  
2. **Hardware Handshake:** Initialized the I2C bus using the `board` module and converted hex strings to integers for the sensor address.  
3. **Gas Stabilization:** Implemented a 5-second "burn-in" delay to ensure the gas resistance heater reaches a stable temperature before logging.  
4. **Persistent Storage:** Created a robust file handler that reads existing logs, appends new data points, and rewrites the file to prevent data loss.

### **5\) Code**

* **Main Script:** `bme680_logger.py`  
* **Example Run:** `python3 bme680_logger.py`

**Data Structure Example:**

JSON

{

    "node\_id": "pi-01",

    "sensor": "bme680",

    "records": \[

        {

            "timestamp": "2026-02-12T14:00:01Z",

            "temperature\_C": 22.5,

            "gas\_resistance\_ohms": 154302.0

        }

    \]

}

### **6\) Testing**

* **Manual Demo:** Checked I2C connectivity with `i2cdetect -y 1`.  
* **Script Test:** Ran the script manually and verified the generated JSON file matched the expected schema.  
* **Result:** **Pass.** Gas resistance readings stabilized as expected after the 5s delay.

### **7\) Lessons & Next Steps**

* **What worked well:** The `try-except` blocks provided clear error messages when the I2C cable was accidentally unplugged.  
* **Problems faced:** JSON file corruption could occur if the script is interrupted mid-write.  
* **Suggestions:** For the next phase, implement an "Atomic Write" (writing to a temp file and then renaming) to prevent data corruption during power loss.

### **8\) References**

* [Adafruit BME680 Python Library Docs](https://docs.circuitpython.org/projects/bme680/en/latest/)  
* \[I2C Communication Overview\]


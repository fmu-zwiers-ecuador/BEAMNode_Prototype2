#**** BEAM PROJECT - FRANCIS MARION UNIVERSITY - DETECT . PY ****#
# This script is meant to use Python's subprocess module to 
# scan SPI, I2C, Camera, and USB sensors and updates config.json
# It should return text detailing which sensors are currently online.
#
# Collaborators:
# Alex Lance | Jaylen Small | Jackson Roberts
#********************************************************************#

#I2C Sensors
# Added bme680 with standard addresses 0x77 and 0x76
I2C_ADDR_TABLE = {
    "tsl2591": [0x29], 
    "aht": [0x38], 
    "bme680": [0x77, 0x76]
}
CANDIDATE_I2C_BUSES = (1,)

def scan_i2c(busnum):
    try:
        result = subprocess.run(["sudo", "i2cdetect", "-y", str(busnum)],
                                capture_output=True, text=True, check=True)
        return result.stdout
    except Exception as e:
        spi_logger.warning(f"I2C scan failed on bus {busnum}: {e}")
        return ""

def detect_i2c_sensors():
    detected = []
    for bus in CANDIDATE_I2C_BUSES:
        if not os.path.exists(f"/dev/i2c-{bus}"):
            continue
        output = scan_i2c(bus)
        found_addrs = set(int(m, 16) for m in re.findall(r"\b[0-9a-f]{2}\b", output, re.IGNORECASE))

        for name, addrs in I2C_ADDR_TABLE.items():
            sensor_found = False
            for addr in addrs:
                if addr in found_addrs:
                    print(f"I2C Sensor Found: {name} (Bus {bus}, Addr 0x{addr:02X})")
                    set_config_flag(CONFIG_PATH, name, "enabled", True)
                    set_config_flag(CONFIG_PATH, name, "i2c_bus", bus)
                    set_config_flag(CONFIG_PATH, name, "address_hex", f"0x{addr:02X}")
                    detected.append(name)
                    sensor_found = True
                    break
            if not sensor_found:
                set_config_flag(CONFIG_PATH, name, "enabled", False)
                set_config_flag(CONFIG_PATH, name, "i2c_bus", None)
                set_config_flag(CONFIG_PATH, name, "address_hex", None)
    if not detected:
        print("No I2C sensors detected")
    return detected

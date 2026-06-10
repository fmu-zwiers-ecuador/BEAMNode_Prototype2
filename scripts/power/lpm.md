# Low Power Mode
The purpose behind low power mode is to turn off sensors based on the voltage read from the battery via the mppt or the pv pi depending on which is connected to the pi. 
The low_power_mode.py script is for the mppt and how that works is it takes the voltage from the mppt and turns off all sensors by changing the enabled field from "true" to "false" when the voltage is at/or below a certain threshold. The script will then turn all sensors back on depending on the threshold as well.
The Lpm_pvpi.py script is for the pv pi. This one does the exact same just with the pv pi.

In config.json, `low_power_mode.enabled` or `lpm_pvpi.enabled` controls whether the launcher starts that low-power monitor. `low_power_active` is the runtime state that says whether the node is currently in low-power mode. `low_power_disabled_sensors` records which sensors were turned off by low-power mode so only those sensors are restored later.

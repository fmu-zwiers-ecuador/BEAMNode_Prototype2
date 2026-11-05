# SCRIPT TO SCHEDULE AND EXECUTE SENSOR READING - scheduler.py

This script should should read frequency values from 'config.json' in minutes, then execute
each sensor on a node periodically after the specified number of minutes has passed.

IMPORTANT: every sensor in config.json should be named exactly the same as the directory they run under. Otherwise,
scheduler.py will not be able to find the script, and won't function correctly.

scheduler.py will only execute scripts from sensors marked as enabled in config.json. Detect.py should write to 
config,json and adjust accordingly.

## Sensor timeouts

Each scheduled sensor can define an optional positive numeric `timeout_sec` in
its `config.json` section. An explicit value always takes precedence.

- Sensors without `timeout_sec` default to 30 seconds.
- Scheduled audio without `timeout_sec` uses `duration_sec + 30` seconds so the
  recorder has time to finish and write its output.
- Invalid or non-positive timeout values are logged and replaced by the
  applicable default.

If a sensor exceeds its timeout, scheduler.py logs the failure and continues to
the next sensor. The timed-out sensor remains on its normal frequency schedule.

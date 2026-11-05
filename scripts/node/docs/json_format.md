# JSON FORMAT OUTLINE:
{
  "node_id": "node_id",
  "sensor": "sensor_used",
  "enabled" : [true/false], ---- Boolean value, dictates whether sensor is enabled
  "frequency" : [int minutes], ---- Dictates how often the sensor is run, in minutes
  "timeout_sec" : [optional positive number], ---- Maximum script runtime; defaults to 30
  ## Next step will vary depending on sensor used, keep syntax ('{}'s and '[]'s) consistent. ##
  "records" : [
    {
        "timestamp" : "time_recorded",
        "data1" : "data_recorded",
        "data2" : "data_recorded"
        
    },
    {
        "timestamp" : "time_recorded",
        "data1" : "data_recorded",
        "data2" : "data_recorded"
    }
  ]
}

For the `audio` section, omitting `timeout_sec` makes the scheduler use
`duration_sec + 30` seconds. Setting `timeout_sec` explicitly overrides that
audio-specific default.

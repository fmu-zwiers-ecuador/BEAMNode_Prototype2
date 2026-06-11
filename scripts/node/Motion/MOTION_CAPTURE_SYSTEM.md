# BEAM Motion Capture System

This folder contains the motion-triggered camera/audio workflow for the BEAM node.
The system watches the PIR sensor, captures high-resolution photos, records a
short video with synchronized audio, and sends final MP4 processing to a
background merge worker.

On a normal node install, `launcher.py` is the only process owner for motion
capture. It starts and monitors both the PIR trigger and the merge worker.

## Main Scripts

- `beam_motion_trigger.py` runs continuously and watches the PIR motion sensor.
- `camera_motion_capture.py` manages the Pi camera, still photos, video capture,
  flash control, and raw H.264 remuxing.
- `motion_merge_worker.py` processes queued audio/video merge jobs into final
  MP4 files.
- `launcher.py` starts `motion_merge_worker.py --watch` by default.
- `beam-motion-merge.service` is optional standalone mode only. Do not enable it
  on normal nodes, or it can duplicate launcher-managed merge processing.
- `install_motion_merge_service.sh` installs standalone mode only when run with
  `--standalone`.
- `disable_standalone_motion_services.sh` stops old standalone motion services so
  launcher remains the only motion owner.
- `motion_logging.py` sets up shared logging to `/home/pi/logs/motion.log`.

## Event Flow

When motion is detected:

1. `beam_motion_trigger.py` creates an event folder under:

   ```text
   /home/pi/data/motion_events/event_TIMESTAMP/
   ```

2. It checks the latest lux reading to decide whether flash should be used.

3. It captures three full-resolution still photos before video.

4. It starts live audio recording first with `arecord`.

5. It waits for the configured audio pre-roll, then starts video recording.

6. It records the video for `motion_capture.duration_sec`.

7. It records timing metadata for audio/video sync.

8. It writes a `.merge.json` job file into the event `combined/` folder.

9. It returns to the PIR loop so the node can wait for the next motion event.

10. The launcher-managed `motion_merge_worker.py --watch` process picks up the
    merge job and creates the final MP4 in the background.

## Event Folder Layout

Each event is stored like this:

```text
/home/pi/data/motion_events/event_TIMESTAMP/
    images/
        motionpic_TIMESTAMP_1.jpg
        motionpic_TIMESTAMP_2.jpg
        motionpic_TIMESTAMP_3.jpg
    video/
        motionvid_TIMESTAMP.h264
        motionvid_TIMESTAMP.mp4
    audio/
        motionaudio_TIMESTAMP.wav
        motionaudio_TIMESTAMP.json
    combined/
        motionvid_audio_TIMESTAMP.merge.json
        motionvid_audio_TIMESTAMP.merge.running.json
        motionvid_audio_TIMESTAMP.merge.done.json
        motionvid_audio_TIMESTAMP.merge.failed.json
        motionvid_audio_TIMESTAMP.mp4
```

The `.merge.*.json` files are job/status files used for background processing and
recovery. The final combined video is `motionvid_audio_TIMESTAMP.mp4`.

## Current Capture Settings

The important settings live in `scripts/node/config.json`.

Current intended behavior:

```json
"motion_capture": {
  "duration_sec": 10,
  "start_delay_sec": 1.0,
  "audio_preroll_sec": 1.0,
  "audio_postroll_sec": 0.5,
  "audio_sync_offset_sec": 1.0
}
```

```json
"camera": {
  "expected_model": "imx708",
  "pir_response_profile": "instant",
  "pir_sensitivity_profile": "high",
  "motion_delay_profile": "fast",
  "detection_range_profile": "widest",
  "pir_warmup_sec": 5,
  "video_bitrate": 5000000,
  "video": {
    "resolution": [1280, 720],
    "fps": 16
  },
  "pictures": {
    "resolution": [4608, 2592],
    "count": 3,
    "mode": "before_video"
  }
}
```

The photos use the Pi Camera Module 3 full 12MP still resolution. The video uses
a lighter 720p setting so the Pi Zero can keep up more reliably.

## PIR Motion Sensitivity

`beam_motion_trigger.py` uses the same camera PIR tuning keys as the older camera
scripts. `pir_sensitivity_profile` controls detection range/sensitivity:

- `high`: most sensitive, threshold `0.3`, sample rate `30`
- `widest`: sensitive, threshold `0.4`, sample rate `20`
- `medium`: default, threshold `0.5`, sample rate `10`
- `narrow`: least sensitive, threshold `0.7`, sample rate `8`

`pir_response_profile` controls how quickly the trigger loop reacts and cools
down:

- `instant`: poll interval `0.02`, cooldown `0.25`
- `fast`: poll interval `0.05`, cooldown `0.5`
- `normal`: poll interval `0.1`, cooldown `1.0`
- `slow`: poll interval `0.2`, cooldown `2.0`

Older aliases still work: `motion_delay_profile` is used if
`pir_response_profile` is missing, and `detection_range_profile` is used if
`pir_sensitivity_profile` is missing. Explicit values such as
`pir_sample_rate`, `pir_queue_len`, `pir_threshold`, `pir_poll_interval_sec`, and
`cooldown_sec` override the selected profiles.

## Low Power Behavior

The launcher reloads `config.json` during its monitor loop. If low-power mode
sets `low_power_active` or disables `motion_capture`, the launcher stops the
motion trigger and merge worker instead of restarting them. The motion trigger
also reloads config while armed and exits cleanly if low-power mode becomes
active.

## Lux-Based Exposure

Before each motion event, `beam_motion_trigger.py` can read the TSL2591 sensor
directly using `camera.live_lux_on_motion`. This live read is only used for
camera exposure/flash decisions and does not append a record to the TSL lux log.
If the live read fails, it falls back to the latest logged TSL lux value.

```json
"lux_exposure_profiles": [
  {"name": "night_or_dark", "max_lux": 50, "photo_ae_enabled": true, "video_ae_enabled": true},
  {"name": "shade", "min_lux": 50, "max_lux": 1000, "photo_ae_enabled": true, "video_ae_enabled": true},
  {"name": "daylight", "min_lux": 1000, "max_lux": 10000, "photo_exposure_us": 2500},
  {"name": "bright_sun", "min_lux": 10000, "max_lux": 30000, "photo_exposure_us": 600},
  {"name": "extreme_sun", "min_lux": 30000, "photo_exposure_us": 250}
]
```

At around 53,000 lux the `extreme_sun` profile is used. If photos are still too
bright, lower `extreme_sun.photo_exposure_us` toward `100`. If they are too
dark, raise it toward `500`.

## Audio/Video Sync

Audio starts before video. This is intentional.

Motion audio uses `motion_audio.alsa_device`. Set it to `auto` to choose the
AudioMoth/USB capture card from `arecord -l` at runtime. If auto-selection picks
the wrong device, run:

```bash
arecord -l
```

Then set `motion_audio.alsa_device` to the matching device, for example
`plughw:2,0`.

If `motion_audio.enabled` is `false`, the motion trigger still captures photos
and video. It logs that audio is disabled and keeps the normal video-only MP4.

The merge worker trims the audio so the final MP4 starts at the video start. The
trim amount is calculated from:

```text
actual video start time - actual audio start time + audio_sync_offset_sec
```

Use `audio_sync_offset_sec` only for fine tuning:

- If audio is ahead of video, increase `audio_sync_offset_sec`.
- If audio is late, decrease `audio_sync_offset_sec`.

Example:

```json
"audio_sync_offset_sec": 1.0
```

means the merge trims one extra second from the front of the audio.

## Video FPS and Smoothness

The camera script records raw H.264 first. After recording, it counts the actual
number of H.264 frames with `ffprobe` and remuxes the video using the measured
FPS. This prevents playback speed drift when the Pi cannot hit the configured
FPS exactly.

Watch for this line in `/home/pi/logs/motion.log`:

```text
Measured video fps ...
```

If the measured FPS is far lower than the configured FPS, the Pi is overloaded.
Lowering video resolution, FPS, bitrate, or exposure time may help.

## Flash Behavior

Flash is controlled by GPIO from `camera.flash_gpio`.

Current config:

```json
"flash_enabled": true,
"flash_gpio": 26,
"flash_lux_threshold": 10
```

The trigger reads the latest lux value from:

```text
/home/pi/data/tsl2591/lux_data.json
```

If lux is below `flash_lux_threshold`, flash is armed for the event.

During photos:

- The flash turns on before each photo.
- It waits `motion_photo_flash_warmup_sec`.
- The photo is captured.
- It waits `motion_photo_flash_cooldown_sec`.
- The flash turns off.

During video:

- If flash is active, it turns on when video starts.
- It stays on for `motion_video_flash_duration_sec`, defaulting to the video
  duration if not configured.
- It is always turned off in cleanup.

If the lux file is missing, the script logs a warning and does not use flash for
that event.

## Background Final Video Processing

The capture script does not run ffmpeg directly for the final MP4. It only writes
a merge job file:

```text
combined/motionvid_audio_TIMESTAMP.merge.json
```

The launcher-managed `motion_merge_worker.py --watch` process watches for these
jobs and processes them independently. This keeps the camera/PIR script free to
return to watching for motion.

For a normal node, do not install `beam-motion-merge.service`. The fresh-clone
setup installs `beamnode.service`, which runs `launcher.py`; launcher starts the
merge worker itself.

To make sure old standalone services are not running:

```bash
cd /home/pi/BEAMNode_Prototype2/scripts/node/Motion
./disable_standalone_motion_services.sh
sudo systemctl restart beamnode.service
```

Optional standalone mode only:

```bash
cd /home/pi/BEAMNode_Prototype2/scripts/node/Motion
./install_motion_merge_service.sh --standalone
```

Check standalone service status:

```bash
sudo systemctl status beam-motion-merge.service
journalctl -u beam-motion-merge.service -f
```

## Merge Recovery

The merge worker is designed to survive restarts.

Job file lifecycle:

```text
.merge.json          queued
.merge.running.json  currently processing
.merge.done.json     completed successfully
.merge.failed.json   failed
```

If the Pi reboots during a merge, the worker will find old
`.merge.running.json` files on startup and retry them.

The worker writes to a temporary file first:

```text
motionvid_audio_TIMESTAMP.mp4.tmp
```

Only after ffmpeg succeeds does it rename the temp file to the final MP4. This
helps avoid leaving a corrupted final MP4 if power is lost during processing.

## Logs

Main capture log:

```text
/home/pi/logs/motion.log
```

Video/final-MP4 processing log:

```text
/home/pi/logs/motion_video_processing.log
```

The motion log shows capture behavior: motion detection, photos, video/audio
recording, measured FPS, and job handoff.

The video processing log shows final MP4 processing: queue pickup, lock waiting,
merge start, completion, failures, ffmpeg stderr, and interrupted job recovery.

## Troubleshooting

### Audio is ahead of video

Increase:

```json
"audio_sync_offset_sec"
```

### Audio is late

Decrease:

```json
"audio_sync_offset_sec"
```

### Video is choppy

Check the measured FPS in `/home/pi/logs/motion.log`.

If measured FPS is much lower than configured FPS, lower the video workload.
Good Pi Zero settings are usually closer to:

```json
"video": {
  "resolution": [1280, 720],
  "fps": 15
}
```

or:

```json
"video": {
  "resolution": [1280, 720],
  "fps": 16
}
```

### Final MP4 is missing

Check:

```text
/home/pi/logs/motion_video_processing.log
```

Also check whether the merge job is stuck or failed in the event `combined/`
folder.

### Merge says WAITING_FOR_LOCK

Only one merge runs at a time. If it is waiting for the lock, another merge is
currently processing or recovering. This is normal for back-to-back events.

### Flash does not turn on

Check:

- `camera.flash_enabled`
- `camera.flash_gpio`
- `camera.flash_lux_threshold`
- whether `/home/pi/data/tsl2591/lux_data.json` exists and has recent lux data

If lux is unavailable, flash is skipped for safety.

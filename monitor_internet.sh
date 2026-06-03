#!/bin/bash

LOG_FILE="/home/pi/logs/internet_monitor.log"
ONLINE=true

mkdir -p "$(dirname "$LOG_FILE")"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

log "Internet monitor started."

while true; do
  if ! ping -c 1 -W 5 8.8.8.8 >/dev/null 2>&1 || ! ping -c 1 -W 5 google.com >/dev/null 2>&1; then
    ONLINE=false
    
    log "$(date): Ping failed. Restarting internet..."
    /home/pi/BEAMNode_Prototype2/restart_internet.sh

    log "Ping failed. Restarting internet."

    /home/pi/BEAMNode_Prototype2/restart_internet.sh >> "$LOG_FILE" 2>&1
  else
    if [ "$ONLINE" = false ]; then
      log "Internet connection restored."
      ONLINE=true
    fi
    sleep 10
  fi
done

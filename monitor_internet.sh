#!/bin/bash

while true; do
  if ! ping -c 1 -W 5 8.8.8.8 >/dev/null 2>&1 || ! ping -c 1 -W 5 google.com >/dev/null 2>&1; then
    echo "$(date): Ping failed. Restarting internet..."
    /home/pi/BEAMNode_Prototype2/restart_internet.sh

    # Give the connection time to recover
    sleep 60
  else
    sleep 10
  fi
done

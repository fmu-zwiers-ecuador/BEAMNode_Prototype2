#!/usr/bin/env bash
set -euo pipefail

SERVICES=(
  "beam-motion-merge.service"
  "motio_camera.service"
)

echo "Stopping standalone motion services so launcher.py is the only motion owner..."

for service in "${SERVICES[@]}"; do
  if systemctl list-unit-files "$service" >/dev/null 2>&1; then
    echo "Disabling $service"
    sudo systemctl disable --now "$service" 2>/dev/null || true
  else
    echo "$service is not installed"
  fi
done

sudo systemctl daemon-reload

echo "Done. Restart launcher with:"
echo "  sudo systemctl restart beamnode.service"

#!/bin/bash
sleep 5
MOUNT_POINT="/Users/brainlee/gdrive_reports"
mkdir -p "$MOUNT_POINT"
mount | grep -q "$MOUNT_POINT" && exit 0
for i in $(seq 1 6); do
    curl -s --connect-timeout 3 http://127.0.0.1:8765/ >/dev/null 2>&1 && break
    sleep 5
done
mount_webdav http://127.0.0.1:8765/ "$MOUNT_POINT"

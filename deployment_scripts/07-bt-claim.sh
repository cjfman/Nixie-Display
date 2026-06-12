#!/bin/bash
# Speaker connects with A2DP but PulseAudio won't create a card. Two questions:
# (1) does PulseAudio run per-user (as nixie) or system-wide (as 'pulse')?
#     -> decides the one-time root group fix if we need one.
# (2) can we force the per-device module to load (no root)?
# Keep the output short. Output -> ~/logs/runonce.log.

MAC=$(bluetoothctl paired-devices 2>&1 | awk '/Device/{print $2; exit}')
DEVPATH="/org/bluez/hci0/dev_$(echo "$MAC" | tr ':' '_')"
echo "device: ${MAC:-none}"

echo "--- PA runs as (nixie = per-user, pulse = system mode) ---"
ps -o user=,comm= -C pulseaudio 2>&1 | sort -u
pactl info 2>&1 | grep -iE "user name"

echo "--- force-load the device module (no root) ---"
[[ -n "$MAC" ]] && pactl load-module module-bluez5-device path="$DEVPATH" 2>&1

echo "--- cards + sinks now ---"
pactl list short cards 2>&1
pactl list sinks short 2>&1
echo "done"

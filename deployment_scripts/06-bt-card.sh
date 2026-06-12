#!/bin/bash
# BT modules are loaded and the speaker connects, but PulseAudio creates no
# bluetooth card -> no sink. Restart PulseAudio (no root) so module-bluez5-
# discover re-scans the already-connected device, then reconnect and dump the
# device's connected profiles (we need to see "Audio Sink" / A2DP) plus the
# resulting cards. Output -> ~/logs/runonce.log.

MAC=$(bluetoothctl paired-devices 2>&1 | awk '/Device/{print $2; exit}')
echo "paired device: ${MAC:-none}"

echo "--- restart PulseAudio (no root) ---"
systemctl --user restart pulseaudio.service 2>&1 || { pulseaudio -k 2>&1; sleep 1; pulseaudio --start 2>&1; }
sleep 4
pactl info 2>&1 | grep -i "server name"

echo "--- (re)connect the speaker ---"
[[ -n "$MAC" ]] && { bluetoothctl connect "$MAC" 2>&1; sleep 6; }

echo "--- speaker's connected profiles (want 'Audio Sink' / A2DP, Connected: yes) ---"
[[ -n "$MAC" ]] && bluetoothctl info "$MAC" 2>&1 | grep -iE "Connected|Name:|Audio|UUID"

echo "--- pulseaudio cards + sinks ---"
pactl list short cards 2>&1
pactl list sinks short 2>&1

echo "--- groups that can gate audio/bluetooth ---"
getent group audio bluetooth lp 2>&1
id

echo "done"

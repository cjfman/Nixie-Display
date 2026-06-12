#!/bin/bash
# Built-in audio works now (nixie is in the audio/bluetooth groups). Try to get
# the Bluetooth speaker to appear as a PulseAudio sink: make sure it's connected,
# restart PulseAudio so module-bluez5-discover re-scans with the new group
# permissions, force the A2DP profile, and report. Output -> ~/logs/runonce.log.

MAC=$(bluetoothctl paired-devices 2>&1 | awk '/Device/{print $2; exit}')
echo "speaker: ${MAC:-none}"

echo "--- connect the speaker ---"
[[ -n "$MAC" ]] && { bluetoothctl connect "$MAC" 2>&1; sleep 6; }

echo "--- restart PulseAudio (picks up the new group membership) ---"
systemctl --user restart pulseaudio.service 2>&1 || { pulseaudio -k 2>&1; sleep 1; pulseaudio --start 2>&1; }
sleep 4

echo "--- ensure bluetooth discovery is loaded, then reconnect ---"
pactl list short modules 2>&1 | grep -qi bluez5-discover || pactl load-module module-bluetooth-discover 2>&1
[[ -n "$MAC" ]] && { bluetoothctl connect "$MAC" 2>&1; sleep 5; }

echo "--- bluez card + A2DP profile ---"
CARD=$(pactl list short cards 2>&1 | awk '/blue/{print $2; exit}')
echo "bluez card: ${CARD:-none}"
[[ -n "$CARD" ]] && pactl set-card-profile "$CARD" a2dp_sink 2>&1

echo "--- result: sinks ---"
pactl list short sinks 2>&1
echo "If a bluez_* sink is listed, pick it in Select Output. If 'bluez card: none'"
echo "even now, PulseAudio still isn't claiming the speaker and we look at PA's log."

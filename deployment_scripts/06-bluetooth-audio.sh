#!/bin/bash
# The audio server is plain PulseAudio 14.2 (not PipeWire). Only a Dummy Output
# exists because PulseAudio's Bluetooth sink isn't being created. Bluetooth audio
# does NOT use the ALSA devices (/dev/snd), so we can get the speaker working
# WITHOUT root and without the 'audio' group: load PulseAudio's bluetooth module
# (pactl needs no sudo), persist it in the user's PulseAudio config, reconnect
# the speaker, and select its A2DP profile. Output -> ~/logs/runonce.log.
# (Onboard/HDMI audio would still need `sudo usermod -aG audio nixie`; the
# speaker does not.)

echo "--- bluetooth module already loaded? ---"
pactl list short modules 2>&1 | grep -i blue || echo "  none"

echo "--- load module-bluetooth-discover (runtime, no root) ---"
pactl load-module module-bluetooth-discover 2>&1 || echo "  (already loaded?)"

echo "--- persist it in the user's PulseAudio config (no root) ---"
mkdir -p "$HOME/.config/pulse"
CFG="$HOME/.config/pulse/default.pa"
if [[ ! -f "$CFG" ]]; then
    { echo ".include /etc/pulse/default.pa"; echo "load-module module-bluetooth-discover"; } > "$CFG"
elif ! grep -qs bluetooth-discover "$CFG"; then
    echo "load-module module-bluetooth-discover" >> "$CFG"
fi
echo "config: $CFG"

echo "--- reconnect the paired speaker ---"
MAC=$(bluetoothctl paired-devices 2>&1 | awk '/Device/{print $2; exit}')
echo "paired device: ${MAC:-none}"
[[ -n "$MAC" ]] && bluetoothctl connect "$MAC" 2>&1
sleep 5

echo "--- select the A2DP profile on the bluetooth card ---"
CARD=$(pactl list short cards 2>&1 | awk '/blue/{print $2; exit}')
echo "bluetooth card: ${CARD:-none}"
[[ -n "$CARD" ]] && pactl set-card-profile "$CARD" a2dp_sink 2>&1

echo "--- result: sinks ---"
pactl list sinks short 2>&1

echo "If a bluez_* sink appears, pick it in Select Output (the pairing flow also"
echo "auto-routes). If not, the lines above tell us the next step."

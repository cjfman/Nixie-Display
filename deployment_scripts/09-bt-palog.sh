#!/bin/bash
# A2DP connect fails (org.bluez.Error.Failed) and PulseAudio makes no bluez card
# => PulseAudio isn't registering an A2DP audio endpoint with BlueZ. Find out why:
# check for a bluealsa conflict (it would steal the endpoint), capture
# PulseAudio's own debug log, and grep it for the bluetooth errors.
# Output -> ~/logs/runonce.log.

MAC=$(bluetoothctl paired-devices 2>&1 | awk '/Device/{print $2; exit}')
echo "speaker: ${MAC:-none}"

echo "=== bluealsa present? (would steal the A2DP endpoint from PulseAudio) ==="
for b in bluealsa bluealsad; do echo "  $b: $(command -v "$b" || echo no)"; done
systemctl is-active bluealsa 2>&1 | sed 's/^/  bluealsa.service: /'
pgrep -a -f bluealsa 2>&1 || echo "  no bluealsa process"

echo "=== speaker profiles (want 'Audio Sink' / A2DP, Connected: yes) ==="
[[ -n "$MAC" ]] && bluetoothctl info "$MAC" 2>&1 | grep -iE "Name:|Connected|UUID|Audio"

echo "=== restart PulseAudio with debug logging ==="
PALOG="$HOME/logs/pa_debug.log"; : > "$PALOG"
systemctl --user stop pulseaudio.socket pulseaudio.service 2>/dev/null
pulseaudio -k 2>/dev/null; sleep 1
pulseaudio --start --log-target="file:$PALOG" --log-level=debug 2>&1
sleep 4
[[ -n "$MAC" ]] && { bluetoothctl connect "$MAC" 2>&1; sleep 5; }

echo "=== PulseAudio bluetooth log lines (the smoking gun) ==="
grep -iE "blue|a2dp|endpoint|transport|profile|module-bluez" "$PALOG" 2>&1 | tail -40
echo "=== ...or from the journal, if systemd-managed ==="
journalctl --user -u pulseaudio --no-pager 2>&1 | grep -iE "blue|a2dp|endpoint" | tail -20

echo "=== bluez card now? ==="
pactl list short cards 2>&1 | grep -i blue || echo "  no bluez card"
echo done

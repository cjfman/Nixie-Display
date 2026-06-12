#!/bin/bash
# Still only a "Dummy Output" means no PipeWire session manager is running.
# On Bullseye, `wireplumber` isn't in the main repo (it's Bookworm/backports),
# so 02's `apt install wireplumber` likely failed. The Bullseye-native session
# manager is `pipewire-media-session`. Install whichever applies, plus the
# Bluetooth SPA plugin, enable it, and report the resulting sinks.
# Output -> ~/logs/runonce.log (Logs > runonce).
export DEBIAN_FRONTEND=noninteractive

echo "--- ensure Bluetooth audio plugin + audio group ---"
sudo apt-get install -y libspa-0.2-bluetooth 2>&1
sudo usermod -aG audio "$USER" 2>&1

if command -v wireplumber >/dev/null 2>&1; then
    SM=wireplumber
    echo "wireplumber is installed; enabling it"
else
    echo "wireplumber not available; installing pipewire-media-session"
    sudo apt-get install -y pipewire-media-session 2>&1
    SM=pipewire-media-session
fi
echo "session manager: $SM (path: $(command -v "$SM" || echo MISSING))"

echo "--- enable + (re)start user audio services ---"
systemctl --user --now enable pipewire pipewire-pulse "$SM" 2>&1
systemctl --user restart pipewire "$SM" 2>&1
sleep 3

echo "--- result ---"
systemctl --user is-active pipewire pipewire-pulse "$SM" 2>&1
pactl info 2>&1 | grep -iE 'server name|default sink'
pactl list sinks short 2>&1

echo "NOTE: reboot once more (so the audio group + session manager start clean),"
echo "then check Audio Diagnosis for a real sink and reconnect the speaker."

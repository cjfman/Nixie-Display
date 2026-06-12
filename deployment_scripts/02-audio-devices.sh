#!/bin/bash
# Real audio devices. PipeWire + pipewire-pulse are running, but with NO session
# manager installed (wireplumber: NOT FOUND) PipeWire enumerates no hardware and
# falls back to a single "Dummy Output" — so there are no real sinks and the BT
# speaker never registers. Install WirePlumber + the Bluetooth SPA plugin, enable
# the user services, and add the user to the audio group so onboard + Bluetooth
# sinks appear. Output is captured to ~/logs/runonce.log (Logs > runonce).
export DEBIAN_FRONTEND=noninteractive

echo "--- apt-get update ---"
sudo apt-get update -y

echo "--- install wireplumber + libspa-0.2-bluetooth ---"
sudo apt-get install -y wireplumber libspa-0.2-bluetooth
echo "install exit: $?"

echo "--- add $USER to the audio group (applies on next reboot) ---"
sudo usermod -aG audio "$USER"

echo "--- enable + start the PipeWire user services ---"
systemctl --user --now enable pipewire pipewire-pulse wireplumber 2>&1
sleep 3

echo "--- result ---"
command -v wireplumber || echo "wireplumber STILL NOT FOUND (may need pipewire-media-session instead)"
systemctl --user is-active pipewire pipewire-pulse wireplumber 2>&1
pactl info 2>&1 | grep -iE 'server name|default sink'
pactl list sinks short 2>&1

echo "NOTE: reboot once more so the audio group and services come up cleanly,"
echo "then check Audio Diagnosis for a real (non-dummy) sink. Re-connect the"
echo "speaker via Add Bluetooth so it registers as a sink."

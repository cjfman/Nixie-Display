#!/bin/bash
# Diagnostic only — makes NO changes. The 02/03 installs failed because sudo
# needs a password in the run-once context, so before the next step we need
# facts: what's installed, whether this user can reach the sound card (the
# 'audio' group), and what (if anything) passwordless sudo allows.
# Output -> ~/logs/runonce.log (Logs > runonce).

echo "--- user + groups (need 'audio' for ALSA device access) ---"
id

echo "--- passwordless sudo ---"
sudo -n true 2>&1 && echo "sudo -n true: OK" || echo "sudo -n true: NO (password required)"
echo "NOPASSWD-allowed commands:"
sudo -n -l 2>&1 | sed -n '1,20p'

echo "--- ALSA sound cards present? ---"
cat /proc/asound/cards 2>&1
aplay -l 2>&1 | grep -i card || echo "  aplay: no cards listed"

echo "--- audio packages installed? ---"
for p in pipewire pipewire-pulse pipewire-media-session wireplumber pulseaudio pulseaudio-module-bluetooth; do
    if dpkg -s "$p" 2>/dev/null | grep -q "ok installed"; then echo "$p: installed"; else echo "$p: NO"; fi
done

echo "--- session-manager binaries ---"
ls -1 /usr/bin/wireplumber /usr/bin/pipewire-media-session 2>&1

echo "--- audio user units available ---"
systemctl --user list-unit-files 2>&1 | grep -iE 'pipewire|pulse|wireplumber|media-session'

echo "--- current server ---"
pactl info 2>&1 | grep -iE 'server name|server version'
echo "--- done ---"

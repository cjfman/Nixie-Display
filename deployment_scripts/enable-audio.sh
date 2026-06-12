#!/bin/bash
# One-shot: enable the user audio session at boot and diagnose what audio stack
# is installed/running. raspi_run redirects output here -> ~/logs/runonce.log
# (read it on the display under Logs > runonce). Delete or edit+repush when done.

echo "whoami: $(id)"

# Enable lingering so the user systemd manager (and PipeWire/PulseAudio) run at
# boot without a login. Non-interactive sudo first, then the unprivileged form.
if sudo -n loginctl enable-linger "$USER" 2>&1; then
    echo "enable-linger: ok (sudo)"
elif loginctl enable-linger "$USER" 2>&1; then
    echo "enable-linger: ok (no sudo)"
else
    echo "enable-linger: FAILED (need root or a polkit self-linger rule)"
fi

export XDG_RUNTIME_DIR="/run/user/$(id -u)"
echo "XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR"
echo "runtime dir:"; ls -ld "$XDG_RUNTIME_DIR" 2>&1
ls -1 "$XDG_RUNTIME_DIR" 2>&1 | grep -iE 'pulse|pipewire' || echo "  (no pulse/pipewire socket yet)"

echo "--- installed? ---"
for p in pipewire pipewire-pulse wireplumber pulseaudio pactl paplay; do
    echo "$p: $(command -v "$p" || echo 'NOT FOUND')"
done

echo "--- user services (active / enabled) ---"
systemctl --user is-active  pipewire pipewire-pulse wireplumber pulseaudio 2>&1
systemctl --user is-enabled pipewire pipewire-pulse wireplumber pulseaudio 2>&1

echo "--- try starting them (if installed) ---"
systemctl --user start pipewire pipewire-pulse wireplumber 2>&1 || true
sleep 2

echo "--- pactl ---"
pactl info 2>&1
pactl list sinks short 2>&1

echo "NOTE: pactl may still show no server in THIS run (linger/services usually"
echo "take effect next boot). Reboot, then check Audio Diag. The lines that"
echo "matter most are the 'installed?' and 'user services' sections above."

#!/bin/bash
# One-time install of the WiFi access-point stack and lock it OFF at boot.
# Needs the NOPASSWD drop-in from `scripts/setup_audio_perms.sh --nopasswd`.
#
# The AP is toggled on demand by scripts/wifi_ap_root.sh (start/stop, never
# enable), so it stays OFF after a reboot but survives a run_display restart.
# This script just installs the packages, makes sure they never auto-start at
# boot, and ensures sshd is up so SSH works over the AP.
#
# stdout/stderr are already redirected into ~/logs/runonce.log by raspi_run.

if ! sudo -n true 2>/dev/null; then
    echo "wifi-ap: passwordless sudo not available."
    echo "wifi-ap: run 'bash scripts/setup_audio_perms.sh --nopasswd' on the Pi first."
    exit 0
fi

echo "wifi-ap: installing hostapd + dnsmasq"
sudo -n apt-get install -y hostapd dnsmasq

echo "wifi-ap: locking hostapd/dnsmasq OFF at boot (AP is start-on-demand only)"
sudo -n systemctl unmask hostapd
sudo -n systemctl disable hostapd dnsmasq

echo "wifi-ap: ensuring sshd is enabled (so SSH works over the AP)"
sudo -n systemctl enable --now ssh

echo "wifi-ap: done"

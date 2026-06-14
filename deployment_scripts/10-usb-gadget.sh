#!/bin/bash
# Arm USB OTG ethernet-gadget SSH access on the next boot. Thin wrapper that runs
# the (root-requiring) helper via passwordless sudo. Needs the NOPASSWD drop-in
# from `scripts/setup_audio_perms.sh --nopasswd` to be in place first, otherwise
# `sudo -n` can't run and this logs how to fix it.
#
# Idempotent: the helper only fills in whatever is missing, so re-runs are no-ops.
# A reboot is required after the first successful run for the gadget to come up.
#
# stdout/stderr are already redirected into ~/logs/runonce.log by raspi_run.

NIXIE_DIR="$HOME/Nixie-Display"
GADGET="$NIXIE_DIR/scripts/usb_gadget_root.sh"

if [[ ! -x "$GADGET" ]]; then
    echo "usb-gadget: helper not found or not executable: $GADGET"
    exit 0
fi

if sudo -n "$GADGET"; then
    echo "usb-gadget: applied (reboot to bring up the gadget)"
else
    echo "usb-gadget: could not run helper with passwordless sudo."
    echo "usb-gadget: run 'bash scripts/setup_audio_perms.sh --nopasswd' on the Pi"
    echo "usb-gadget: (over your current WiFi/SSH) to arm $GADGET, then retry."
fi

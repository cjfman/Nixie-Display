#!/bin/bash
# Enable USB OTG ethernet-gadget (g_ether) so the Pi can be reached over SSH on a
# direct USB cable when no WiFi is available. Plug the data micro-USB port (USB,
# not PWR) into a host; the Pi enumerates as a USB ethernet adapter, both ends
# take a link-local 169.254.x.x address, and it is reachable at <hostname>.local.
#
# Requires root because it edits the boot partition. Run it by hand over SSH:
#   sudo scripts/usb_gadget_root.sh
# It is idempotent -- re-running it only fills in whatever is missing. The
# deployment_scripts/ run-once channel invokes this via `sudo -n`, but only the
# root-owned copy that setup_audio_perms.sh --nopasswd installs to
# /usr/local/sbin/ is NOPASSWD-whitelisted (this in-repo copy is not).
#
# A REBOOT is required after the first run for the kernel module to load.
#
# Env overrides (mainly for testing off-Pi):
#   USB_GADGET_BOOT_DIR   boot partition to edit (default: autodetect /boot*)
#                         setting this also enables "test mode": no root check,
#                         no systemctl, no chown.
#   USB_GADGET_MARKER     marker file to write (default: ~nixie/.nixie/usb_gadget_enabled)
#   NIXIE_USER            display service user that owns the marker (default: nixie)
set -u

NIXIE_USER="${NIXIE_USER:-nixie}"
MODULE="modules-load=dwc2,g_ether"

usage() {
    sed -n '2,/^set -u/p' "$0" | sed 's/^# \{0,1\}//; /^set -u/d'
}

test_mode() {
    ## Returns success when an explicit boot dir is set, i.e. we're testing
    ## off-Pi and must not require root or touch real services.
    [[ -n "${USB_GADGET_BOOT_DIR:-}" ]]
}

detect_boot_dir() {
    if [[ -n "${USB_GADGET_BOOT_DIR:-}" ]]; then
        echo "$USB_GADGET_BOOT_DIR"
    elif [[ -f /boot/firmware/config.txt ]]; then
        echo "/boot/firmware"
    elif [[ -f /boot/config.txt ]]; then
        echo "/boot"
    else
        echo ""
    fi
}

backup_once() {
    ## Keep a one-time pristine copy so a bad edit is recoverable from another
    ## machine (mount the SD card, restore the .bak).
    local file="$1"
    if [[ -f "$file" ]] && [[ ! -f "$file.bak" ]]; then
        cp "$file" "$file.bak"
        echo "backed up $(basename "$file") -> $(basename "$file").bak"
    fi
}

ensure_config_overlay() {
    local config="$1"
    if grep -q '^dtoverlay=dwc2' "$config"; then
        echo "config.txt: dtoverlay=dwc2 already present"
    else
        printf '\n# nixie usb gadget\ndtoverlay=dwc2\n' >> "$config"
        echo "config.txt: added dtoverlay=dwc2"
    fi
}

ensure_cmdline_module() {
    ## cmdline.txt is a single space-separated line; never add a newline mid-line.
    ## Done in pure bash (portable) rather than `sed -i`, which differs GNU/BSD.
    local cmdline="$1" content
    content=$(cat "$cmdline")
    if [[ "$content" == *"$MODULE"* ]]; then
        echo "cmdline.txt: $MODULE already present"
        return
    fi
    if [[ "$content" == *rootwait* ]]; then
        content="${content/rootwait/rootwait $MODULE}"
    else
        content="$content $MODULE"
    fi
    printf '%s\n' "$content" > "$cmdline"
    echo "cmdline.txt: added $MODULE"
}

enable_services() {
    ## ssh = the thing we want to reach; avahi-daemon = makes <hostname>.local
    ## resolvable over the link-local gadget link.
    if test_mode; then
        echo "services: skipped (test mode)"
        return
    fi
    local unit
    for unit in ssh avahi-daemon; do
        if systemctl enable "$unit" >/dev/null 2>&1; then
            echo "services: enabled $unit"
        else
            echo "services: could not enable $unit (may not exist)"
        fi
    done
}

write_marker() {
    ## The SSH Access menu (runs as $NIXIE_USER) reads this to decide whether to
    ## show USB reachability or "Not available".
    local marker="${USB_GADGET_MARKER:-}"
    if [[ -z "$marker" ]]; then
        local home
        home=$(getent passwd "$NIXIE_USER" | cut -d: -f6)
        marker="$home/.nixie/usb_gadget_enabled"
    fi
    mkdir -p "$(dirname "$marker")"
    date > "$marker"
    if ! test_mode; then
        chown "$NIXIE_USER:$NIXIE_USER" "$(dirname "$marker")" "$marker" 2>/dev/null || true
    fi
    echo "marker: wrote $marker"
}

main() {
    if [[ "${1:-}" == "-h" ]] || [[ "${1:-}" == "--help" ]]; then
        usage
        exit 0
    fi

    if ! test_mode && [[ $EUID -ne 0 ]]; then
        echo "Must be run as root (try: sudo $0)" >&2
        exit 1
    fi

    local boot config cmdline
    boot=$(detect_boot_dir)
    if [[ -z "$boot" ]]; then
        echo "Could not find a boot partition (no /boot/config.txt or /boot/firmware/config.txt)" >&2
        exit 1
    fi
    config="$boot/config.txt"
    cmdline="$boot/cmdline.txt"
    echo "Using boot dir: $boot"

    backup_once "$config"
    backup_once "$cmdline"
    ensure_config_overlay "$config"
    ensure_cmdline_module "$cmdline"
    touch "$boot/ssh"
    echo "ssh: touched $boot/ssh"
    enable_services
    write_marker

    echo
    echo "USB gadget setup complete. REBOOT REQUIRED for the change to take effect."
    echo "After reboot, plug the USB (data) port into a host and: ssh ${NIXIE_USER}@\$(hostname).local"
}

main "$@"

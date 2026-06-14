#!/bin/bash
# One-time, root-requiring audio setup for the nixie display.
#
# Run this ON THE PI while logged in (it elevates with sudo as needed), because
# the deployment_scripts/ run-once channel can't -- its sudo needs a password.
# It grants the display's service user the group memberships PulseAudio needs to
# claim ALSA (HDMI) and Bluetooth audio, which is the missing piece behind the
# "Dummy Output only / speaker won't appear" problem.
#
#   bash scripts/setup_audio_perms.sh                  # default user 'nixie'
#   bash scripts/setup_audio_perms.sh someuser         # a different display user
#   bash scripts/setup_audio_perms.sh --nopasswd       # also let future
#                                                      # deployment scripts run
#                                                      # loginctl + the usb-gadget
#                                                      # & wifi-ap helpers
#                                                      # unattended (NOT apt-get,
#                                                      # usermod, or systemctl)
#
# Reboot afterwards for the group changes to take effect.
set -u

USER_NAME="nixie"
NOPASSWD=0
INSTALL_KEYS=0
for arg in "$@"; do
    case "$arg" in
        --nopasswd)     NOPASSWD=1 ;;
        --install-keys) INSTALL_KEYS=1 ;;
        --*)            echo "unknown option: $arg" ;;
        *)              USER_NAME="$arg" ;;
    esac
done

SUDO=""
[[ $EUID -ne 0 ]] && SUDO="sudo"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$SCRIPT_DIR")"

if ! id "$USER_NAME" >/dev/null 2>&1; then
    echo "No such user: $USER_NAME" >&2
    exit 1
fi
echo "Display service user: $USER_NAME"

## Install the run-once signing public key(s) so the deployment_scripts/ channel
## is verified. NO sudo needed (it's $USER_NAME's home). Doing this turns ON
## signature enforcement: from then on only signed scripts run. Do it via this
## trusted terminal session (not the git channel itself). Run with --install-keys.
if [[ $INSTALL_KEYS -eq 1 ]]; then
    HOME_DIR=$(getent passwd "$USER_NAME" | cut -d: -f6)
    DEST="$HOME_DIR/.nixie/runonce_keys"
    if compgen -G "$REPO/keys/runonce/*.pem" >/dev/null; then
        $SUDO -u "$USER_NAME" mkdir -p "$DEST"
        $SUDO -u "$USER_NAME" cp "$REPO"/keys/runonce/*.pem "$DEST"/
        echo "Installed public key(s) to $DEST :"
        ls -1 "$DEST"
        echo ">>> Signature enforcement is now ON. Only signed deployment scripts run."
    else
        echo "No public keys found at $REPO/keys/runonce/*.pem" >&2
    fi
fi

add_groups() {
    ## Add $1 to the audio-related groups that exist. audio = /dev/snd (ALSA,
    ## HDMI); bluetooth = BlueZ control/media; lp = legacy rfcomm.
    local who="$1" grp
    for grp in audio bluetooth lp; do
        if getent group "$grp" >/dev/null; then
            echo "  adding $who to '$grp'"
            $SUDO usermod -aG "$grp" "$who"
        fi
    done
}

echo "Granting groups to $USER_NAME:"
add_groups "$USER_NAME"

## If PulseAudio runs system-wide (as user 'pulse'), it -- not $USER_NAME --
## is what needs Bluetooth/audio access.
PA_USER=$(ps -o user= -C pulseaudio 2>/dev/null | sort -u | grep -vx "$USER_NAME" | head -1)
if [[ "${PA_USER:-}" == "pulse" ]]; then
    echo "PulseAudio is running system-mode as 'pulse'; granting it groups too:"
    add_groups pulse
fi

## Make sure the user manager (and PulseAudio) start at boot without a login.
$SUDO loginctl enable-linger "$USER_NAME" 2>/dev/null || true

## Optional: unblock the run-once deployment scripts so they can arm the
## USB-gadget / WiFi-AP helpers and re-assert linger without a password next
## time. Deliberately narrow: NOT apt-get, usermod, or systemctl. Granting the
## unattended, branch-pushed deployment channel package installs (apt-get runs
## maintainer scripts as root), group changes (usermod), or unit control
## (systemctl) is effectively root -- keep those terminal-only, here.
if [[ $NOPASSWD -eq 1 ]]; then
    USER_HOME=$(getent passwd "$USER_NAME" | cut -d: -f6)
    GADGET="$USER_HOME/Nixie-Display/scripts/usb_gadget_root.sh"
    WIFI_AP="$USER_HOME/Nixie-Display/scripts/wifi_ap_root.sh"
    echo "Installing NOPASSWD sudoers drop-in for $USER_NAME (loginctl + usb gadget + wifi ap)"
    DROPIN=/etc/sudoers.d/nixie-deploy
    printf '%s ALL=(ALL) NOPASSWD: /usr/bin/loginctl, %s, %s\n' \
        "$USER_NAME" "$GADGET" "$WIFI_AP" | $SUDO tee "$DROPIN" >/dev/null
    $SUDO chmod 0440 "$DROPIN"
    $SUDO visudo -cf "$DROPIN" || echo "WARNING: sudoers check failed; review $DROPIN"
fi

echo
echo "Done. Groups for $USER_NAME are now:"
id "$USER_NAME"
echo
echo "Next: reboot (sudo reboot). After it comes up, the speaker should appear"
echo "in Select Output (reconnect it via Add Bluetooth if needed)."

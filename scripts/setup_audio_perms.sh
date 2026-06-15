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
if [[ $EUID -ne 0 ]]; then
    SUDO="sudo"
fi
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$SCRIPT_DIR")"

if ! id "$USER_NAME" >/dev/null 2>&1; then
    echo "No such user: $USER_NAME" >&2
    exit 1
fi
USER_UID="$(id -u "$USER_NAME")"
RUNTIME_DIR="/run/user/$USER_UID"
echo "Display service user: $USER_NAME (uid $USER_UID)"

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

## Run a command inside $USER_NAME's user session with the env PulseAudio needs.
as_display_user() {
    $SUDO -u "$USER_NAME" env \
        XDG_RUNTIME_DIR="$RUNTIME_DIR" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=${RUNTIME_DIR}/bus" \
        "$@"
}

start_user_session() {
    ## Bring $USER_NAME's user manager up now so /run/user/<uid> exists this
    ## session too (linger handles it at every future boot).
    $SUDO systemctl start "user@${USER_UID}.service"
    local i
    for i in $(seq 1 10); do
        if [[ -d "$RUNTIME_DIR" ]]; then
            break
        fi
        sleep 1
    done
    if [[ -d "$RUNTIME_DIR" ]]; then
        echo "  $RUNTIME_DIR exists"
    else
        echo "  WARNING: $RUNTIME_DIR still missing after starting user@${USER_UID}.service"
    fi
}

enable_pulse() {
    ## Enable + start $USER_NAME's per-user PulseAudio. Without this the display
    ## (which runs as $USER_NAME) has NO audio server at all -- the real reason
    ## only 'Dummy Output' ever showed and no Bluetooth sink could appear.
    if as_display_user systemctl --user unmask pulseaudio.socket pulseaudio.service >/dev/null 2>&1; then
        :
    fi
    if as_display_user systemctl --user enable pulseaudio.socket >/dev/null 2>&1; then
        echo "  enabled pulseaudio.socket for $USER_NAME"
    else
        echo "  WARNING: could not enable pulseaudio.socket (will still try to start)"
    fi
    if as_display_user pactl info >/dev/null 2>&1; then
        echo "  $USER_NAME PulseAudio is responding"
    else
        echo "  pactl couldn't reach PA; trying an explicit start"
        as_display_user systemctl --user start pulseaudio.service >/dev/null 2>&1
    fi
}

verify_audio() {
    local info mods
    info="$(as_display_user pactl info 2>&1)"
    if printf '%s' "$info" | grep -q "User Name: $USER_NAME"; then
        echo "  $USER_NAME PulseAudio is up:"
        printf '%s\n' "$info" | grep -E 'User Name|Server Version|Default Sink' | sed 's/^/    /'
    else
        echo "  WARNING: $USER_NAME PA still not reachable. Raw output:"
        printf '%s\n' "$info" | sed 's/^/    /'
    fi
    mods="$(as_display_user pactl list short modules 2>/dev/null | grep -i bluez)"
    if [[ -n "$mods" ]]; then
        echo "  Bluetooth modules loaded:"
        printf '%s\n' "$mods" | sed 's/^/    /'
    else
        echo "  WARNING: no bluez modules loaded (pulseaudio-module-bluetooth missing?)"
    fi
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
if ! $SUDO loginctl enable-linger "$USER_NAME" 2>/dev/null; then
    echo "  WARNING: enable-linger failed; PA may not start at boot"
fi

## Bring the session up now and enable $USER_NAME's own PulseAudio.
echo "Starting $USER_NAME user session:"
start_user_session
echo "Enabling $USER_NAME PulseAudio:"
enable_pulse

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
echo "Verifying $USER_NAME audio server:"
verify_audio

echo
echo "Done. Groups for $USER_NAME are now:"
id "$USER_NAME"
echo
echo "Next: reboot (sudo reboot). After it comes up, the speaker should appear"
echo "in Select Output (reconnect it via Add Bluetooth if needed)."
echo
echo "Note: while you are logged in as 'pi', pi's own PulseAudio is also running"
echo "and can hold the single Bluetooth A2DP endpoint. On a normal headless boot"
echo "pi isn't logged in, so only $USER_NAME's PA runs. To test BT now without a"
echo "reboot, first stop pi's PA:  systemctl --user stop pulseaudio.socket pulseaudio.service; pulseaudio -k"

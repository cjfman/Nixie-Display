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

set_pa_resident() {
    ## Keep $USER_NAME's PA from auto-exiting when idle. The default is socket
    ## activation with a ~20s idle timeout, so PA only flickers to life per
    ## `pactl` call and then quits -- which means there's no resident daemon to
    ## hold the Bluetooth A2DP endpoint, so the speaker's connect never completes
    ## and it drops back to discoverable. exit-idle-time = -1 == never auto-exit.
    local home conf
    home="$(getent passwd "$USER_NAME" | cut -d: -f6)"
    if [[ -z "$home" ]]; then
        return
    fi
    conf="$home/.config/pulse/daemon.conf"
    $SUDO -u "$USER_NAME" mkdir -p "$home/.config/pulse"
    if $SUDO -u "$USER_NAME" grep -qs '^exit-idle-time' "$conf"; then
        echo "  exit-idle-time already set in daemon.conf"
    else
        printf 'exit-idle-time = -1\n' | $SUDO -u "$USER_NAME" tee -a "$conf" >/dev/null
        echo "  set exit-idle-time = -1 in $conf"
    fi
}

enable_pulse() {
    ## Enable + start $USER_NAME's per-user PulseAudio, RESIDENT. Without this the
    ## display (which runs as $USER_NAME) has no audio server holding the BT
    ## endpoint -- the reason only 'Dummy Output' showed and the speaker would
    ## pair but never connect.
    if as_display_user systemctl --user unmask pulseaudio.socket pulseaudio.service >/dev/null 2>&1; then
        :
    fi
    set_pa_resident
    ## Start at boot and keep it resident. add-wants pins pulseaudio.service into
    ## default.target even though the packaged unit has no [Install] section, so
    ## linger -> default.target -> PA every boot (not just on first socket use).
    as_display_user systemctl --user enable pulseaudio.socket >/dev/null 2>&1
    as_display_user systemctl --user add-wants default.target pulseaudio.service >/dev/null 2>&1
    ## (Re)start now so it picks up exit-idle-time and registers the BT endpoint.
    as_display_user systemctl --user restart pulseaudio.service >/dev/null 2>&1
    sleep 1
    if as_display_user pactl info >/dev/null 2>&1; then
        echo "  $USER_NAME PulseAudio is responding"
    else
        echo "  WARNING: PA not responding; trying pulseaudio --start"
        as_display_user pulseaudio --start --exit-idle-time=-1 >/dev/null 2>&1
    fi
}

quiet_other_pulse() {
    ## Any OTHER user running its own PulseAudio (e.g. an auto-login console user
    ## like 'pi') competes with $USER_NAME for the single Bluetooth A2DP media
    ## endpoint -- only one process per adapter can own it, so the loser's
    ## connect fails with org.bluez.Error.Failed and no card appears. The display
    ## user needs audio; a login/console user does not. Mask + stop those PAs and
    ## set autospawn=no so nothing respawns one. ('pulse' = system-mode daemon,
    ## handled separately above, so it's excluded here.)
    local others u uid home
    others="$(ps -o user= -C pulseaudio 2>/dev/null | sort -u \
              | grep -vx "$USER_NAME" | grep -vx pulse)"
    if [[ -z "$others" ]]; then
        echo "  no competing PulseAudio instances"
        return
    fi
    for u in $others; do
        quiet_one_pulse "$u"
    done
}

quiet_one_pulse() {
    local u="$1" uid home
    if ! id "$u" >/dev/null 2>&1; then
        return
    fi
    uid="$(id -u "$u")"
    home="$(getent passwd "$u" | cut -d: -f6)"
    echo "  silencing $u's PulseAudio (uid $uid)"
    $SUDO -u "$u" env XDG_RUNTIME_DIR="/run/user/$uid" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" \
        systemctl --user mask pulseaudio.socket pulseaudio.service >/dev/null 2>&1
    $SUDO -u "$u" env XDG_RUNTIME_DIR="/run/user/$uid" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" \
        systemctl --user stop pulseaudio.socket pulseaudio.service >/dev/null 2>&1
    $SUDO -u "$u" env XDG_RUNTIME_DIR="/run/user/$uid" pulseaudio -k >/dev/null 2>&1
    ## autospawn=no so no client respawns it after the mask.
    if [[ -n "$home" ]]; then
        $SUDO -u "$u" mkdir -p "$home/.config/pulse"
        if ! $SUDO -u "$u" grep -qs '^autospawn' "$home/.config/pulse/client.conf"; then
            printf 'autospawn = no\n' | $SUDO -u "$u" tee -a "$home/.config/pulse/client.conf" >/dev/null
        fi
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
    ## The whole point of this round: PA must be RESIDENT, not autospawn-and-die.
    if pgrep -u "$USER_NAME" -x pulseaudio >/dev/null 2>&1; then
        echo "  PA is resident (process running for $USER_NAME) -- good"
    else
        echo "  WARNING: no resident $USER_NAME PA process; it will not hold the"
        echo "           Bluetooth endpoint. Check exit-idle-time / add-wants above."
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
echo "Silencing competing PulseAudio (other users):"
quiet_other_pulse

## Optional: unblock the run-once deployment scripts so they can arm the
## USB-gadget / WiFi-AP helpers and re-assert linger without a password next
## time. Deliberately narrow: NOT apt-get, usermod, or systemctl. Granting the
## unattended, branch-pushed deployment channel package installs (apt-get runs
## maintainer scripts as root), group changes (usermod), or unit control
## (systemctl) is effectively root -- keep those terminal-only, here.
if [[ $NOPASSWD -eq 1 ]]; then
    SBIN=/usr/local/sbin
    GADGET="$SBIN/usb_gadget_root.sh"
    WIFI_AP="$SBIN/wifi_ap_root.sh"
    ## Install the root helpers OUTSIDE the git tree, root-owned and NOT writable
    ## by $USER_NAME. NOPASSWD points only at these copies, so a `git pull` (which
    ## rewrites the in-repo scripts on every boot) -- or anyone who gets a shell as
    ## $USER_NAME via the very SSH/AP access these enable -- can't silently change
    ## what runs as root. Updating the helpers = re-run this script.
    echo "Installing root helpers to $SBIN (root-owned, outside the repo):"
    $SUDO install -o root -g root -m 0755 "$REPO/scripts/usb_gadget_root.sh" "$GADGET"
    $SUDO install -o root -g root -m 0755 "$REPO/scripts/wifi_ap_root.sh" "$WIFI_AP"
    echo "  $GADGET"
    echo "  $WIFI_AP"
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
echo "Competing per-user PulseAudio instances have been masked, so only"
echo "$USER_NAME runs an audio server (and owns the Bluetooth A2DP endpoint)."
echo
echo "Next: reboot (sudo reboot). After it comes up, the speaker should appear"
echo "in Select Output (reconnect it via Add Bluetooth if needed). A reboot is"
echo "the clean way to let $USER_NAME's PA claim the endpoint now that the"
echo "competing instance is gone."

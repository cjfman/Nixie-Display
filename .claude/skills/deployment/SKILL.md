---
name: deployment
description: How the Nixie display is deployed and configured on the Raspberry Pi — the nixie-live branch + raspi_run (with its self-rewrite/re-exec hazard), the deployment_scripts/ run-once channel and its RSA signing, on-Pi diagnostics via the Logs menu, the PulseAudio audio stack and its permission requirements, and the one-time root setup script. Use when changing raspi_run, adding deployment/setup scripts, debugging why something doesn't run or audio doesn't work on the Pi, or wiring signing.
---

# Pi deployment & on-Pi configuration

The production system is a headless Raspberry Pi Zero 2 (Raspbian **Bullseye**,
Python 3.9.2). SSH and attaching a screen are both hard (network policy /
headless), so most fixes go through git + the boot script. The display itself is
the main UI; commands are typed via the keyboard menu (NixieShell, read-only) and
results read off the tubes or the **Logs** menu.

## Deploy path

- The Pi runs the **`nixie-live`** branch. `master` is dev. **Deploy = merge
  `master` → `nixie-live`** and push (use `git merge`, not cherry-pick). The Pi
  pulls `nixie-live` on every boot.
- `raspi_run` (repo root) is the boot script: export session env → `git checkout
  nixie-live; git pull` → **re-exec itself** → pip install if changed → run
  deployment scripts → launch `run_display`.

## raspi_run self-rewrite hazard (important)

`git pull` rewrites `raspi_run` **while bash is executing it**. Bash reads scripts
by byte offset, so a changed file makes execution resume at a stale offset and
**silently skip whole sections** — the classic symptom is "a later block runs but
an earlier one is skipped" (e.g. `run_display` launches but the deployment hook
never ran). **Fix in place:** after `git pull`, raspi_run does
`exec bash "$NIXIE_DIR/raspi_run" --reexec`, so everything below runs from a clean
read of the up-to-date file. This also removed the old two-boot bootstrap lag — a
pushed change now applies on the next reboot.
**Never let a long-running script git-pull/rewrite itself without re-exec.**

**The re-exec only happens when HEAD actually changed.** If `git pull` brought in
new commits, raspi_run execs itself with `--no-pull` so the next run reads the
updated file from the top and skips the pull step. If nothing changed, git didn't
touch the file, there is no byte-offset hazard, and the script continues without
exec-ing. Arguments are parsed with a loop (not positional checks) so `--no-pull`
can appear anywhere. `--no-pull` is not exported, so it cannot be inherited by any
subprocess; the daemon that restarts raspi_run provides its own environment and
launches a bare `raspi_run` that always pulls. The log marker `(pulled=0/1)`
shows whether new code was deployed.

raspi_run captures its own output to `~/logs/raspi_run.log` (Logs > raspi_run).
`send_text`/`run_display` talk to the display directly, not stdout, so the
redirect is safe.

## deployment_scripts/ — run-once channel

`raspi_run` runs every `deployment_scripts/*.sh` **once**, keyed by content hash
(state `~/.nixie/runonce_state/<name>.hash`), in filename order (prefix `NN-`),
with output → `~/logs/runonce.log` (Logs > runonce). Edit + repush → new hash →
runs again. `deployment_scripts/template.sh.example` is the template (the
`.example` suffix keeps it out of the `*.sh` glob). Implemented by
`run_deploy_script()` in raspi_run.

**The run-once scripts run as `nixie`, whose sudo needs a PASSWORD** (NOPASSWD
only for `halt`/`reboot`/`iwlist`/`wpa_cli`). So they **cannot** `apt install` or
`usermod` — those fail "a password is required". Root-requiring setup goes in
**`scripts/setup_audio_perms.sh`** (run at a terminal): adds `nixie` to
audio/bluetooth/lp groups, re-asserts `loginctl enable-linger`,
`--install-keys` (install signing public key), `--nopasswd` (scoped sudoers
drop-in so future deployment scripts *can* install).

## RSA signing of the channel

`pyxielib/runonce_sig.py` + `bin/sign_runonce` (dev) + `bin/verify_runonce` (Pi).
`run_deploy_script` runs `verify_runonce --keys-dir ~/.nixie/runonce_keys` before
each script and skips on failure. **Permissive until a public key is installed
there, then strict** (only signed scripts run; invalid signature is a hard skip;
success logs the matching key file's stem). Signed bytes = the file minus
`# nixie-runonce-` header lines (so headers don't affect the signature).

- Private key: off-repo, gitignored (`~/.nixie_runonce/charles_priv.pem`).
- Public key: committed at `keys/runonce/charles.pem`.
- `setup_audio_perms.sh --install-keys` copies it to `~/.nixie/runonce_keys/`
  from a trusted terminal — this turns enforcement ON.
- Sign: `bin/sign_runonce --key ~/.nixie_runonce/charles_priv.pem --name charles deployment_scripts/foo.sh`
- **Order: install keys + sign scripts BEFORE enabling `--nopasswd`**, else a
  branch push is unsigned root.

## USB OTG SSH fallback

When no WiFi is available, the Pi can be reached over a direct USB cable: the
Zero 2 W's data micro-USB port (**`USB`**, not `PWR`) runs in OTG gadget mode and
enumerates as a USB ethernet adapter (`g_ether`) on the host — one cable carries
both power and data. Both ends self-assign link-local `169.254.x.x`, so it's
reachable at `<hostname>.local` (avahi/mDNS), no router involved.

Setup is three **root-owned, boot-partition** changes, all done idempotently by
`scripts/usb_gadget_root.sh`: `dtoverlay=dwc2` in `config.txt`,
`modules-load=dwc2,g_ether` (inserted after `rootwait`) in `cmdline.txt`, and
`systemctl enable ssh avahi-daemon` + `touch <boot>/ssh`. It backs up
`config.txt`/`cmdline.txt` to `*.bak` first, writes a marker
`~/.nixie/usb_gadget_enabled`, and prints **REBOOT REQUIRED** (the module only
loads on the next boot). It detects `/boot` (Bullseye) vs `/boot/firmware`
(Bookworm); `USB_GADGET_BOOT_DIR` overrides it for off-Pi testing.

Two ways to apply it:
- **By hand over SSH:** `sudo scripts/usb_gadget_root.sh` (it requires root).
- **Unattended via the run-once channel:** `deployment_scripts/10-usb-gadget.sh`
  is a thin `sudo -n` wrapper. Since editing `/boot` needs root and the run-once
  user's sudo needs a password, `scripts/setup_audio_perms.sh --nopasswd` now
  also whitelists `usb_gadget_root.sh` in the sudoers drop-in — run that once
  (over current WiFi) to arm it, then deploy. If unarmed, the script logs how to
  fix it to `runonce.log` and exits cleanly.

The **SSH Access** menu (`SSHAccessMenu` in `menu_library.py`) shows the SSH
hostname, the `usb0` address (or "Not available" when the marker is absent /
"Connect USB cable" when armed but no cable), and the WiFi address.

Recovery: if a bad `cmdline.txt` edit ever fails to boot, pull the SD card and
restore `cmdline.txt.bak` on the boot partition.

## WiFi AP fallback (on-demand, menu-toggled)

When neither external WiFi nor USB is available, the Pi can broadcast its own
WPA2 network (`hostapd` + `dnsmasq`): join it from a laptop/phone and
`ssh nixie@192.168.4.1`. The Zero 2 W has **one radio**, so the AP **replaces**
client WiFi while up; turning it off restores client mode.

**Persistence model (the key design point):** the AP is toggled with
`systemctl start/stop` — **never `enable`** — so the running service *is* the
state. A `run_display` restart leaves `hostapd`/`dnsmasq` running → **AP
persists**; a **reboot** doesn't start them (they're left disabled) → **AP off on
boot**. No marker file, no startup hook.

- `scripts/wifi_ap_root.sh {up <conf>|down|status}` (root, NOPASSWD-whitelisted)
  owns the mechanics: `up` generates `/etc/hostapd/hostapd.conf` (+ points
  `/etc/default/hostapd`'s `DAEMON_CONF` at it) and `/etc/dnsmasq.d/nixie-ap.conf`,
  frees `wlan0` (`dhcpcd -k wlan0`), sets static `192.168.4.1/24`, starts the
  services. `down` stops them and **`dhcpcd -n wlan0`** rebinds so client WiFi
  autoconnects again (no reboot). It only touches `wlan0`, so a USB gadget on
  `usb0` is unaffected. SSID/password come from a `0600` conf file (off the
  argv). `WIFI_AP_ETC_DIR` overrides `/etc` for off-Pi testing.
- `deployment_scripts/11-wifi-ap.sh` (run-once, needs `--nopasswd` armed)
  installs the packages, `unmask`s + **`disable`s** them at boot, and enables
  `ssh`.
- Config: `wifi_ap: {ssid, password}` in the master YAML (defaults
  `nixie-control` / `neon-tube-backdoor-64`; validated 1–32 / 8–63 chars,
  invalid → default). Wired via `make_wifi_ap_config` → `WiFiAPConfig` →
  `WiFiAPController` (`pyxielib/wifi_ap_controller.py`).
- Toggle: `WiFiAPItem` (a `[y/n]`-confirm state machine) appears in **both** the
  WiFi Settings and SSH Access menus.

## Audio stack (the long debugging saga)

The audio server is **plain PulseAudio 14.2, NOT PipeWire** (`pipewire` is
installed but `pipewire-pulse` is not). Do not install `wireplumber` /
`pipewire-media-session` — dead ends (wireplumber isn't in Bullseye anyway).

Requirements that bit us, in order:
1. **Session reachability** — `pactl` needs `XDG_RUNTIME_DIR=/run/user/<uid>` +
   the user manager running (`loginctl enable-linger`). raspi_run exports
   `XDG_RUNTIME_DIR`/`DBUS_SESSION_BUS_ADDRESS`. Empty → "Connection refused" →
   "No audio server".
2. **Group membership** — `nixie` must be in the **`audio`** group (and
   `bluetooth`, `lp`) or PulseAudio can't open `/dev/snd` and shows only a single
   **"Dummy Output" (auto_null)** — no real sink, no default. `setup_audio_perms.sh`
   fixes this (root, one-time). This was the core blocker.
3. **Version gotchas** (fixed in `audio_controller.py`):
   - `pactl get-default-sink` is PulseAudio 15+; on 14.2 parse `pactl info`'s
     `Default Sink:` line. (`set-default-sink` is old and works.)
   - `bluetoothctl devices Paired` filter is BlueZ 5.65+; Bullseye has 5.55 → use
     `bluetoothctl paired-devices`.

Bluetooth: pair in a **single `bluetoothctl` session** (agent is per-session);
verify via `bluetoothctl info` not exit codes. `pulseaudio-module-bluetooth` is
installed; BT audio bypasses `/dev/snd`.

## On-Pi diagnostics

Everything routes to the **Logs** menu (`LogViewerItem`, `TextBodyItem` scrollable
viewer): `stdout` (nixie.log), `stderr`, `runonce` (deployment scripts),
`raspi_run` (launcher). Plus **Audio Settings > Audio Diagnosis** (`AudioDiagItem`)
shows XDG / pactl reachability / sinks live. A diagnostic deployment script that
dumps state to runonce.log, read off the tubes, is how most of this was solved
without SSH.

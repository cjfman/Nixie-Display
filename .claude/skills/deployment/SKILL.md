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

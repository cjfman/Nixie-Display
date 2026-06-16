# Audio / Bluetooth Work — Findings & Open State

Synthesized from session `1181203c` (2026-06-11 → 06-12). Source transcript:
`audio-bluetooth-transcript.md`. This is the durable summary so the work can
resume without replaying the 700k-token session.

---

## ✅ RESOLVED (2026-06-15) — Bluetooth speaker works

**Built-in / HDMI audio: working but irrelevant.** The Pi Zero 2 has **no analog
audio** — its only ALSA output is `VC4-HDMI`. The "Built-in Audio" sink only
appears when something is plugged into HDMI; with HDMI unplugged, **Dummy Output
is the expected state, not a regression.** The display is driven over GPIO/SPI,
so **the Bluetooth speaker is the only usable audio output for this Pi.**

**Bluetooth speaker (Sony SRS-XB100): now connects, routes, and plays.** The
earlier hypotheses (bluealsa conflict, PA bluez module failing to register) were
**both wrong** — the PA shutdown debug log showed `module-bluez5-discover`,
`module-bluetooth-discover`, `module-bluetooth-policy` all load fine, and no
bluealsa is present. The real causes were a stack of session/lifecycle problems,
each found by reading off the tubes:

1. **Wrong user.** The display runs as **`nixie` (uid 1002)**; every pactl/BT test
   was being run as **`pi` (uid 1000)** — a *different* PulseAudio. A2DP card
   creation/routing is **per-PA-instance**, so the user that needs audio was
   never the one being inspected. (`pactl info` → `User Name: pi` was the tell.)
2. **Two PAs fighting for one endpoint.** `pi` **auto-logs in**, so pi's PA runs
   at every boot. Only **one process per adapter** can own the BlueZ A2DP media
   endpoint; pi's grabbed it, nixie's lost, and `bluetoothctl connect` failed with
   exactly **`org.bluez.Error.Failed`** + no card. Fix: mask + stop every *other*
   user's PA and set `autospawn = no` (autologin left intact).
3. **nixie's PA wasn't resident.** Socket-activated PA autospawns per `pactl` call
   and exits on the ~20 s idle timeout, so `pgrep -u nixie pulseaudio` was empty —
   **no resident daemon to hold the A2DP endpoint**, so the speaker *paired but
   never connected* and stayed in discoverable mode. Fix: `exit-idle-time = -1`
   in nixie's `~/.config/pulse/daemon.conf` **and** `systemctl --user add-wants
   default.target pulseaudio.service` so linger starts it (resident) at every boot.
4. **Group membership only applies to a *new* session.** Running the setup script
   added nixie to `audio`/`bluetooth` but its already-running PA didn't have them
   until a **reboot** — so a fix that "ran" needs a reboot to take effect.
5. **Default sink was HDMI.** Once connected, the bluez sink exists but isn't
   default — audio/test went to HDMI. `pactl set-default-sink <bluez>` (Select
   Output does this) routes it; both sinks reading `SUSPENDED` is normal (idle).

All of the above are now handled by **`scripts/setup_audio_perms.sh`** (run once
from a terminal as `pi`; it self-elevates with sudo). Commits: `761e256` (enable
nixie PA), `5b1c9b5` (silence competing PAs), `2360d66` (resident PA +
`_is_connected` requires `Connected: yes`), `0b700c8` (Audio Test fix).

**Audio Test bug (separate):** the menu test `paplay`'d a freedesktop **`.oga`
(Ogg Vorbis)**, which paplay/libsndfile here can't decode → it played the
compressed bytes as raw PCM = **white noise**; its fallback used **`aplay`** (raw
ALSA, bypasses PA, can't reach a bluez sink). Fixed in `0b700c8`: synthesize a
clean WAV tone and `paplay` it (no codec; follows the selected default sink).

### ✔️ VERIFIED ON-DEVICE (2026-06-15, after deploy + reboot)
- Audio Test on the speaker plays a **clean tone**, not static (`0b700c8` good).
- Remove + re-Add Bluetooth **auto-selects** the speaker as the default sink
  (`select_sink_for_mac` fires correctly) — no manual Select Output needed.
- End-to-end working: pair → connect → auto-route → play. Saga closed.

### Running pactl/bt as nixie from a pi shell
A login shell as nixie + manual `export XDG_RUNTIME_DIR=...` gives **`Failed to
create secure directory` / connection refused** unless you really are nixie with
the right HOME. Use the form that works (uid 1002):
```bash
nx() { sudo -u nixie env XDG_RUNTIME_DIR=/run/user/1002 \
       DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1002/bus "$@"; }
nx pactl list short sinks
nx pactl set-default-sink 1                       # index or name
IDX=$(nx pactl load-module module-sine frequency=440); sleep 2; nx pactl unload-module $IDX
```
`module-sine` is a file-free tone generator straight to a sink — the reliable
"does this sink make sound" probe.

### Other loose ends
- **Signing the deployment scripts is incomplete.** The keypair exists
  (private: `~/.nixie_runonce/charles_priv.pem` on the Mac, gitignored; public:
  `keys/runonce/charles.pem` committed) and verification is wired in (permissive
  until a key is on the Pi), but the actual `sign_runonce` loop never ran —
  blocked by a `fork failed: resource temporarily unavailable` limit on the Mac.
  To finish, when forks are free:
  ```bash
  for f in deployment_scripts/*.sh; do
    ./bin/sign_runonce --key ~/.nixie_runonce/charles_priv.pem --name charles "$f"
  done
  git add deployment_scripts && git commit -m "Sign deployment scripts"
  ```
- **`--nopasswd` deliberately deferred** until signing keys are enforced on the
  Pi (otherwise anyone who can push to `nixie-live` gets root). Correct order:
  sign scripts → `setup_audio_perms.sh --install-keys` (via trusted terminal) →
  flip `--nopasswd`. **Scope tightened (2026-06-14):** the `nixie-deploy` sudoers
  drop-in now grants only `loginctl` + the `usb_gadget_root.sh` /
  `wifi_ap_root.sh` paths — **`apt-get`, `usermod`, and `systemctl` were all
  removed** (each is ~root for the unattended channel). Consequence: a future
  fix needing package installs, group changes, or system-service management
  **cannot** go through the deployment_scripts channel; run
  `setup_audio_perms.sh` at a terminal instead. **Note:** restarting PulseAudio
  does NOT need any of this — it's a per-user service
  (`systemctl --user restart pulseaudio`, no sudo).
- **Everything is on `master`, none deployed** unless separately merged to
  `nixie-live`. (The `runonce-signing` branch is the *old* single-`runonce.sh`
  model and is superseded — signing was re-integrated onto master directly.)

---

## 🔑 Durable root causes & version traps (the expensive lessons)

These are the non-obvious things that cost the most debugging time:

1. **`raspi_run` self-rewrite bug.** `raspi_run` does `git pull`, which rewrites
   the very file bash is executing. Bash tracks position by byte offset, so a
   large edit *below* the pull line makes execution resume at a stale offset and
   **silently skip whole sections** (here, the deployment-script hook) while
   later code (`run_display`) still runs. Symptom that pinned it: the hook's
   first line (`mkdir ~/.nixie/runonce_state`) never executed, yet `run_display`
   did — impossible in normal top-to-bottom flow. **Fix:** `raspi_run` now
   **re-execs its freshly-pulled self** after the pull (guarded, once), so the
   rest of the boot reads a stable file; plus it logs to `~/logs/raspi_run.log`
   (viewable at **Logs → raspi_run**). This also killed the old "changes take a
   reboot to apply" lag — deployment scripts now run on the *first* reboot.

2. **No user audio session.** The nixie process launches with **no
   `XDG_RUNTIME_DIR` and no user D-Bus**, so `pactl` got "connection refused"
   even though system tools (`bluetoothctl`) worked. **Fix:** `raspi_run` exports
   `XDG_RUNTIME_DIR=/run/user/$(id -u)` + DBUS; plus `loginctl enable-linger`
   (so the user manager + PulseAudio run at boot without a login). uid is `1002`
   (`nixie`).

3. **The server is plain PulseAudio 14.2 — NOT PipeWire.** A long detour chased
   WirePlumber / `pipewire-media-session` before diagnostics proved
   `pipewire-pulse` was `NO` and the server is PulseAudio 14.2. Do **not** install
   the PipeWire session stack — it would conflict.

4. **`sudo` needs a password** in the run-once context (no terminal). Passwordless
   sudo is scoped to `halt/reboot/iwlist/wpa_cli` only. So **deployment scripts
   cannot `apt install` or `usermod`** — that wall caused several "script ran but
   nothing happened" rounds. Root-requiring setup goes in
   `scripts/setup_audio_perms.sh`, run once from a real terminal.

5. **`nixie` is not in the `audio`/`bluetooth` groups** (groups: kmem, input,
   gpio, spi). `setup_audio_perms.sh` adds audio/bluetooth/lp. Needed for the
   ALSA/HDMI path; may also matter for BT (untested conclusively).

### PulseAudio / BlueZ version gotchas on Bullseye
| Command used | Requires | Bullseye has | Workaround applied |
|---|---|---|---|
| `pactl get-default-sink` | PulseAudio 15+ | 14.2 | parse `Default Sink:` from `pactl info` |
| `bluetoothctl devices Paired` | BlueZ 5.65+ | 5.55 | use `paired-devices` |
These two caused "View Current: Unknown" and an empty Remove-Bluetooth menu
respectively — both fixed.

6. **Spinner / scheduler re-poll bug.** A menu item that shows an animation while
   it needs to keep polling (BT scan timeout) **must return a one-shot animation
   with identity `__eq__`**, not a `LoopedFullFrameAnimation` (whose `done()` is
   always `False`). The scheduler only re-polls the user menu when
   `assembler.animationDone()`, an interrupt, or a cron boundary fires — so a
   never-done loop froze `BTAddItem`'s state machine and the scan "never exited."
   `ProgressSpinner` is now one-shot and rebuilt each cycle (loops visually *and*
   drives one poll per cycle). String states stay re-polled (`should_interrupt`
   true), so timed auto-dismiss works there.

7. **Per-user PA contention is invisible until you check `User Name`.** The
   display runs as `nixie`; `pi` auto-logs in and runs its OWN PulseAudio. Audio
   state (sinks, bluez cards, the A2DP endpoint) is **per-PA-instance**, so
   inspecting the wrong user's PA shows the wrong story, and two PAs competing for
   the single BlueZ media endpoint is exactly what produced `org.bluez.Error.Failed`
   + "no bluez card". Always confirm `pactl info` → `User Name:` is the display
   user, and ensure no *other* user runs a PA (mask it; autologin can stay).

8. **A per-user PA must be made resident for an always-on appliance.** Default
   socket-activation + ~20 s idle timeout means PA isn't running when the BT
   device tries to connect, so it pairs but won't connect. `exit-idle-time = -1`
   + `add-wants default.target pulseaudio.service` (linger already on) makes it
   resident across boots. Test residency with `pgrep`, not `pactl` (which
   autospawns a throwaway instance and hides the problem).

---

## 🛠️ What was built (all committed to `master`)

**Audio menu** (`pyxielib/menu_library.py`, `pyxielib/audio_controller.py`):
- `AudioController` wrapping `pactl` + `bluetoothctl` (`AudioSink`,
  `BluetoothDevice` dataclasses). All pactl calls route through one logging
  helper (throttled) that distinguishes "no audio server" from "no outputs".
- Menu items: View Current, **Select Output** (`AudioSelectItem`), **Add
  Bluetooth** (`BTAddItem`, 10s scan + spinner, filters MAC-only entries,
  pair+trust+connect in a single `bluetoothctl` session verified via
  `bluetoothctl info`), **Remove Bluetooth** (`BTRemoveItem`, Y/N), **Mute**
  (`CycleItem`), **Test Audio** (`AudioTestItem`, `paplay` freedesktop sound →
  1kHz beep fallback), **Audio Diagnosis** (`AudioDiagItem` on `TextBodyItem`).
- `CycleItem` (`navigator.py`) — shared inline-edit control used by both Mute and
  the Tap Revolution Difficulty shortcut; straight-to-edit, blinking value,
  arrows cycle, name auto-hides when `{name} | {value}` would exceed display width
  (uses `cmdLen` so `!`/`{...}` don't count).
- `ProgressSpinner` (`animation_library.py`) — cumulative ring fill like
  `pip_nixie` (`0x80 → 0x180 → … → 0x3fc0`), one-shot (see lesson #6).

**Single-session BT pairing:** `power on → agent on → default-agent → scan on →
pair → trust → connect → scan off`, with waits, a stdout-draining reader thread
(avoids pipe deadlock), success verified by `bluetoothctl info` (`Paired:` /
`Connected: yes`) not exit codes. On success, auto-routes by polling for the
`bluez_output.<mac>` sink and setting it default.

**Deployment infrastructure** (`raspi_run` + `deployment_scripts/`):
- Run-once hook runs every `deployment_scripts/*.sh` once (keyed by content hash,
  state in `~/.nixie/runonce_state/`), output to `~/logs/runonce.log`
  (**Logs → runonce**). `template.sh.example` is the template.
- re-exec-after-pull fix + `raspi_run.log` (see lesson #1).
- RSA signing: `pyxielib/runonce_sig.py`, `bin/sign_runonce`,
  `bin/verify_runonce`; verify wired into the loop (permissive → strict once a
  key is installed in `~/.nixie/runonce_keys/`). Public key committed at
  `keys/runonce/charles.pem`.
- `scripts/setup_audio_perms.sh` — the one canonical one-shot (run as `pi`,
  self-elevates). Now does the **full** audio fix: audio/bluetooth/lp groups,
  enable-linger, **enable nixie's RESIDENT PulseAudio** (`exit-idle-time = -1` +
  `add-wants default.target pulseaudio.service`), **silence every other user's PA**
  (mask + `autospawn = no`, e.g. pi's), and verify (`pactl info` + bluez modules +
  a `pgrep` residency check). Optional `--nopasswd` sudoers drop-in and
  `--install-keys` are unchanged and still off by default. A standalone
  `fix_nixie_audio.sh` was folded into this and deleted.

**Spent diagnostic scripts** `02`–`09` already ran (hashes recorded; won't
re-run). They drove the off-board diagnosis since SSH/screen are hard — the user
relays log readings off the tubes.

---

## Memories already written from this session
`audio-menu-pactl-session`, `raspi-run-reexec`, `deployment-scripts-signing`,
`spinner-scheduler-repoll`, plus the `deployment` skill and CLAUDE.md updates.
This findings file is the task-level companion to those.

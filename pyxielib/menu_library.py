import collections
import copy
import logging
import re
import os
import subprocess
import threading
import time
from typing import List, Optional

from pyxielib import tap_revolution as taplib
from pyxielib.tap_revolution_config import TapRevolutionConfig as _TRConfig
from pyxielib.navigator import DelayedCommandItem, ListItem, Menu, MenuItem, MsgItem, SubcommandItem
from pyxielib.wifi_controller import WiFiController
from pyxielib.animation import Animation, MarqueeAnimation
from pyxielib.animation_file import FileAnimation

logger = logging.getLogger(__name__)


class IpItem(SubcommandItem):
    def __init__(self):
        super().__init__("Show IP Address", "ip route get 8.8.8.8 | head -1 | cut -d' ' -f7", shell=True)

    def run(self) -> str:
        output = super().run().strip()
        match = re.match(r"^\d{1,3}(\.\d{1,3}){3}$", output)
        if match:
            return output

        return "No IP Address"


class GitStatusItem(ListItem):
    """Top-level item showing the repository's branch, HEAD commit, and how it
    compares to its upstream. The list is recomputed each time it is activated.
    """
    FETCH_TIMEOUT = 10

    def __init__(self, **kwargs):
        super().__init__("Git Status", **kwargs)
        self.fetching = False
        self.fetch_failed = False

    def reset(self):
        super().reset()
        self.fetching = False
        self.fetch_failed = False

    def activate(self):
        ## Fetch in the background so the menu stays responsive
        self.fetching = True
        self.fetch_failed = False
        threading.Thread(target=self._load, daemon=True).start()

    def for_display(self) -> str:
        if self.fetching:
            return "Fetching..."
        if self.fetch_failed:
            return "FAILED"
        return super().for_display()

    def _load(self):
        """Fetch from the remote, then build the status list (runs in a thread)."""
        if self._git('fetch', timeout=self.FETCH_TIMEOUT) is None:
            self.fetch_failed = True
            self.fetching = False
            return
        self.set_values(self.git_status())
        self.fetching = False

    @classmethod
    def git_status(cls):
        """Build the list of status lines, or a single line on failure."""
        commit = cls._git('rev-parse', '--short', 'HEAD')
        if commit is None:
            return ["Not a git repo"]
        branch = cls._git('symbolic-ref', '--short', '-q', 'HEAD')
        return [branch or "Detached HEAD", commit, cls._upstream_status()]

    @classmethod
    def _upstream_status(cls):
        """Describe HEAD's position relative to its upstream branch."""
        counts = cls._git('rev-list', '--left-right', '--count', 'HEAD...@{upstream}')
        if counts is None:
            return "No upstream"
        try:
            ahead, behind = (int(x) for x in counts.split())
        except ValueError:
            return "Unknown"
        if not ahead and not behind:
            return "Up to date"
        if ahead and behind:
            return f"{ahead} ahead {behind} behind"
        return f"{ahead} ahead" if ahead else f"{behind} behind"

    @staticmethod
    def _git(*args, timeout=None):
        """Run git in the repo dir; return stripped stdout, or None on failure."""
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        try:
            result = subprocess.run(['git', '-C', repo, *args], capture_output=True, check=False, timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        if result.returncode != 0:
            return None
        return result.stdout.decode('utf8').strip()


class SystemInfoItem(ListItem):
    def __init__(self, **kwargs):
        super().__init__("System Info", **kwargs)

    def activate(self):
        self.set_values(self._gather())

    @staticmethod
    def _gather() -> List[str]:
        items = []
        items.append("OS: " + SystemInfoItem._read_os_release())
        items.append("Kernel: " + (SystemInfoItem._run('uname', '-r') or "N/A"))
        hw = SystemInfoItem._hardware_model()
        items.append("HW: " + (hw or "N/A"))
        ver = SystemInfoItem._run('python3', '--version')
        items.append(ver or "Python: N/A")
        items.append("Up: " + (SystemInfoItem._run('uptime', '-p') or "N/A"))
        items.append("CPU: " + SystemInfoItem._cpu_temp())
        mem = SystemInfoItem._run('sh', '-c', "free -h --si | awk 'NR==2{print $3\"/\"$2}'")
        items.append("Mem: " + (mem or "N/A"))
        disk = SystemInfoItem._run('sh', '-c', "df -h / | awk 'NR==2{print $3\"/\"$2}'")
        items.append("Disk: " + (disk or "N/A"))
        return items

    @staticmethod
    def _run(*args) -> Optional[str]:
        try:
            r = subprocess.run(list(args), capture_output=True, check=False, timeout=5)
            return r.stdout.decode().strip() if r.returncode == 0 else None
        except Exception:
            return None

    @staticmethod
    def _read_os_release() -> str:
        try:
            with open('/etc/os-release') as f:
                for line in f:
                    if line.startswith('PRETTY_NAME='):
                        return line.split('=', 1)[1].strip().strip('"')
        except OSError:
            pass
        return "N/A"

    @staticmethod
    def _hardware_model() -> Optional[str]:
        try:
            with open('/proc/cpuinfo') as f:
                for line in f:
                    if line.startswith('Model'):
                        return line.split(':', 1)[1].strip()
        except OSError:
            pass
        return None

    @staticmethod
    def _cpu_temp() -> str:
        try:
            raw = open('/sys/class/thermal/thermal_zone0/temp').read().strip()
            return f"{int(raw) / 1000:.1f}C"
        except OSError:
            return "N/A"


class RebootItem(DelayedCommandItem):
    def __init__(self, **kwargs):
        super().__init__("Reboot", "sudo reboot", running_msg="Rebooting...", **kwargs)


class ShutdownItem(DelayedCommandItem):
    def __init__(self, **kwargs):
        super().__init__("Shutdown", "sudo halt", running_msg="Shutting down...", **kwargs)


class ExitItem(MenuItem):
    def activate(self):
        raise KeyboardInterrupt()


class SleepItem(MenuItem):
    def __init__(self, controller, **kwargs):
        super().__init__("Sleep Display", **kwargs)
        self.controller = controller

    def activate(self):
        self.controller.disable()

    def key_char(self, c):
        ## pylint: disable=unused-argument
        self.set_done()

    def key_arrow(self, d):
        ## pylint: disable=unused-argument
        self.set_done()

    def set_done(self):
        self.controller.enable()
        super().set_done()


class WiFiScanItem(ListItem):
    def __init__(self, device='wlan0', sudo=True, show_passwd=False, wifi=None, **kwargs):
        super().__init__("Add WiFi Network", **kwargs)
        self.device = device
        self.sudo   = sudo
        self.show   = show_passwd
        self.wifi   = wifi
        self.proc   = None
        self.state  = None
        self.ssid   = None
        self.passwd = None

    def reset(self):
        super().reset()
        self.set_values(None)
        self.proc   = None
        self.state  = None
        self.ssid   = None
        self.passwd = None

    def for_display(self) -> str:
        self.poll()
        msg = ""
        if self.state is None:
            msg = "Scan not started"
        elif 'running' == self.state:
            msg = "Scanning..."
        elif 'select' == self.state:
            msg = super().for_display()
        elif 'password' == self.state:
            if not self.passwd:
                msg = "Enter Password"
            else:
                msg = self.passwd_msg()
        elif 'connected' == self.state:
            msg = "Connected"
        elif 'failed' == self.state:
            msg = "Conn. failed" if self.ssid else "Scan failed"
        else:
            msg = f"Error state: {self.state}"

        return msg

    def passwd_msg(self):
        if self.show:
            return self.passwd

        return '*'*len(self.passwd)

    def activate(self):
        self.run()
        self.state = 'running'

    def run(self):
        cmd = ['iwlist', self.device, 'scan']
        if self.sudo:
            cmd = ['sudo'] + cmd
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)

    def poll(self):
        if self.state != 'running':
            return

        ret = self.proc.poll()
        if ret is None:
            return
        if ret != 0:
            self.state = 'failed'
            return

        networks = set()
        known = set(self.wifi.network_ssids())
        for line in self.proc.stdout:
            match = re.search(r'ESSID:"(.+)"', line.decode('utf8'))
            if match:
                networks.add(match.groups()[0])

        ## Add a header and exclude known networks
        header = f"Found {len(networks)} SSIDs"
        self.set_values([header] + sorted(networks.difference(known)))
        self.state = 'select'

    def key_enter(self):
        if 'select' == self.state and self.idx and self.wifi:
            self.ssid = self.current_value()
            self.state = 'password'
        elif 'password' == self.state:
            success = self.wifi.add_network(self.ssid, self.passwd, save=True, connect=False)
            self.state = 'connected' if success else 'failed'
        elif self.state in ('connected', 'failed'):
            self.reset()
            self.set_done()

    def key_char(self, c):
        if 'password' == self.state:
            if self.passwd is None:
                self.passwd = c
            else:
                self.passwd += c

    def key_backspace(self):
        if 'password' == self.state and self.passwd:
            if len(self.passwd) <= 1:
                self.passwd = None
            else:
                self.passwd = self.passwd[:-1]


class WiFiSelectItem(ListItem):
    def __init__(self, wifi, **kwargs):
        super().__init__("WiFi Select", **kwargs)
        self.wifi = wifi
        self.state = 'select'

    def activate(self):
        self.set_values(self.wifi.network_ssids())

    def for_display(self) -> str:
        self.poll()
        msg = "WiFi Select Err"
        if 'select' == self.state:
            msg = super().for_display()
        elif 'confirm' == self.state:
            msg = 'Set Network[y/n]'
        elif 'success' == self.state:
            msg = 'Connected'
        elif 'failed' == self.state:
            msg = 'Failed'
        elif 'already' == self.state:
            msg = "Connected already"
        elif 'connecting' == self.state:
            msg = 'Connecting...'

        return msg

    def reset(self):
        super().reset()
        self.set_values(None)
        self.state = 'select'

    def poll(self):
        if self.state != 'connecting':
            return

        success = self.wifi.poll()
        if success is not None:
            self.state = 'success' if success else 'failed'

    def select(self):
        self.wifi.select_network(self.current_value(), blocking=False)

    def key_enter(self):
        if self.state == 'select':
            if self.current_value() == self.wifi.connected_to():
                self.state = 'already'
            else:
                self.state = 'confirm'
        elif self.state == 'confirm':
            pass
        elif self.state == 'already':
            self.state = 'select'
        else:
            self.state = 'done'
            self.set_done()

    def key_char(self, c):
        if self.state != 'confirm':
            return

        c = c.lower()
        if c == 'y':
            self.select()
            self.state = 'connecting'
        elif c == 'n':
            self.state = 'select'


class WiFiMenu(Menu):
    def __init__(self):
        super().__init__("WiFi Settings")
        self.wifi = WiFiController('wlan0', sudo=True)

        ## Add submenues
        ssid = lambda: self.wifi.connected_to() or "No Network"
        addr = lambda: self.wifi.ip_address() or "No Address"
        conn = lambda: "Connected" if self.wifi.connected() else "Not Connected"

        self.add_submenu(MsgItem("Current Network", ssid))
        self.add_submenu(MsgItem("IP Address", addr))
        self.add_submenu(MsgItem("Status", conn))
        self.add_submenu(WiFiSelectItem(self.wifi, display_name="Select Network"))
        self.add_submenu(WiFiScanItem(wifi=self.wifi))

    def activate(self):
        super().activate()
        self.wifi.load(force=True)

    def reset(self):
        super().reset()


class MirrorItem(MenuItem):
    """Mirror whatever is typed. Exits on ESC and not backspace"""
    def __init__(self, name="Mirror", **kwargs):
        super().__init__(name, **kwargs, crop=True)
        self.msg = ""
        self._msg = ""
        self.bracket = False

    def for_display(self):
        return self.msg

    def reset(self):
        super().reset()
        self.msg = ""
        self._msg = ""

    def key_char(self, c):
        """Add a key to the message"""
        ## Check for a bracket, as this is a special character
        if c == '{':
            if self.bracket:
                ## We're already in bracket mode
                return

            self.bracket = True
        elif c == '}':
            if not self.bracket:
                ## We are not in bracket mode
                return

            self.bracket = False

        ## Add to the internal message and clone to external one
        self._msg += c
        self.msg = self._msg
        if self.bracket:
            ## Close bracket on external message
            self.msg += '}'

    def key_backspace(self):
        """Erase the last typed key"""
        if not self._msg:
            return

        if self._msg[-1] == '}':
            ## We've entered a bracket section
            self.bracket = True
        elif self._msg[-1] == '{':
            ## We've entered a bracket section
            self.bracket = False

        ## Add to the internal message and clone to external one
        self._msg = self._msg[:-1]
        self.msg = self._msg
        if self.bracket:
            ## Close bracket on external message
            self.msg += '}'


class AnimationLibraryItem(ListItem):
    def __init__(self, path, **kwargs):
        super().__init__("Animations", **kwargs)
        self.path = path
        self.ani_paths = None
        self.selected = None
        ## Parsing a .ani file can be slow, so a selection loads in a background
        ## thread (see key_enter/_load) while for_display shows "Loading...".
        ## _load_id is bumped on every new load and on reset so a load that
        ## finishes after the user has moved on is discarded instead of applied.
        self.loading = False
        self._load_id = 0

    def for_display(self) -> Animation:
        if self.loading:
            return "Loading..."

        if self.selected is None:
            return super().for_display()

        if self.selected.done():
            self.selected = None
            return super().for_display()

        return self.selected

    def reset(self):
        super().reset()
        self.set_values(None)
        self.ani_paths = None
        self.selected = None
        self.loading = False
        self._load_id += 1  ## invalidate any in-flight load

    def activate(self):
        try:
            paths = sorted(x for x in os.listdir(self.path) if x.endswith(".ani") and not x.startswith("_"))
        except OSError:
            return

        ## Label each file by its 'title' command (falling back to the file
        ## name), disambiguating repeats with a '(N)' suffix.
        ani_paths = {}
        counts = {}
        for path in paths:
            name = self._unique_name(FileAnimation.read_title(os.path.join(self.path, path)), counts)
            ani_paths[name] = path

        self.set_values(sorted(ani_paths, key=self.sort_key))
        self.ani_paths = ani_paths

    @staticmethod
    def sort_key(name) -> str:
        """Normalize a menu name into the key it should be ordered by.

        Strips leading marker characters (anything non-alphanumeric, e.g. a '*'
        favorite prefix), drops the '<'/'>' of a '<N>' disambiguation suffix so
        'Rain<2>' sorts next to 'Rain', and lower-cases so titled animations
        interleave with filename-based ones instead of all sorting ahead.
        """
        name = re.sub(r"^[^0-9A-Za-z]+", '', name)
        name = name.replace('<', '').replace('>', '')
        return name.lower()

    @staticmethod
    def _unique_name(title, counts) -> str:
        """Disambiguate a repeated title by appending '<N>'.

        ``counts`` maps each title to how many files have used it so far. The
        first file keeps the bare title; the Nth (N>1) file becomes 'title<N>'
        (e.g. 'Clock', 'Clock<2>', 'Clock<3>'). Angle brackets are used because
        the nixie can render '<'/'>' but not '('/')' or '['/']' (all NOCODE).
        """
        seen = counts.get(title, 0)
        counts[title] = seen + 1
        return title if seen == 0 else f"{title}<{seen + 1}>"

    def _cancel(self):
        """Cancel any in-flight load or playing animation, staying at the list."""
        if self.loading:
            self._load_id += 1
            self.loading = False
        elif self.selected is not None:
            self.selected = None

    def key_enter(self):
        ## Ignore input while a load is in flight or an animation is playing
        if self.loading or self.selected is not None:
            return
        name = self.current_value()
        if name in self.ani_paths:
            self.loading = True
            self._load_id += 1
            path = os.path.join(self.path, self.ani_paths[name])
            threading.Thread(target=self._load, args=(path, self._load_id), daemon=True).start()

    def key_esc(self):
        if self.loading or self.selected is not None:
            self._cancel()
        else:
            self.set_done()

    def key_backspace(self):
        if self.loading or self.selected is not None:
            self._cancel()
        else:
            self.set_done()

    def _load(self, path, load_id):
        """Parse the selected animation (runs in a background thread).

        Discards the result if a newer load started or the item was reset
        (``reset`` bumps ``_load_id``) while this parse was running.
        """
        try:
            animation = FileAnimation(path)
        except Exception:
            animation = None
        if load_id != self._load_id:
            return
        self.selected = animation
        self.loading = False


class ProgramListItem(ListItem):
    def __init__(self, programs, **kwargs):
        super().__init__("Programs", sorted(programs.keys()), **kwargs)
        self.programs = programs
        self.selected = None

    def for_display(self) -> Animation:
        if self.selected is None:
            return super().for_display()

        program = self.selected
        if program.done():
            program.reset()
            return super().for_display()
        elif program.update():
            ## Get the next animation
            return program.getAnimation()

        return None

    def key_enter(self):
        if self.selected is None:
            name = self.current_value()
            if name in self.programs:
                self.selected = self.programs[name]

    def reset(self):
        super().reset()
        if self.selected:
            self.selected.reset()
        self.selected = None


class TapRevolutionMenu(Menu):
    """The Tap Revolution game menu: Play (levels), Settings (view), Reset Settings.

    All three children share one ``TapRevolutionConfig`` so a reset (or a future
    in-game edit) is reflected the next time a level is launched.
    """
    def __init__(self, config, levels_path, *, watcher=None, size=16, **kwargs):
        super().__init__("Tap Revolution", [
            TapRevolutionLevelsItem(config, levels_path, watcher=watcher, size=size),
            TapRevolutionSettingsItem(config),
            ResetSettingsItem(config),
        ], **kwargs)


class TapRevolutionLevelsItem(ListItem):
    """Dance-Dance-Revolution-style rhythm game.

    Lists levels (``.trl`` files in ``levels_path`` plus the built-in
    programmatic charts); selecting one launches a ``TapRevolutionAnimation`` built
    from the shared config. While playing, the configured action keys are taps
    routed into the animation — timestamped by the key watcher so hit timing is
    accurate regardless of poll latency — and list navigation is suspended. When
    the chart finishes (or the player backs out early) a results marquee plays
    before returning to the level list.
    """
    def __init__(self, config, levels_path, *, watcher=None, size=16, **kwargs):
        super().__init__("Play", **kwargs)
        self.config      = config
        self.levels_path = levels_path
        self.watcher     = watcher
        self.size        = size
        self.level_files = {}
        self.animation   = None
        self.results     = None
        self.key_lane    = {}

    def activate(self):
        self.level_files = self._scan_levels()
        self.set_values(sorted(taplib.BUILTIN_LEVELS) + sorted(self.level_files))

    def _scan_levels(self):
        """Map each .trl file's 'name:' title to its path; built-ins added separately.

        Repeated titles are disambiguated with a '<N>' suffix, like
        AnimationLibraryItem (angle brackets render on the nixie; parens don't).
        """
        try:
            files = sorted(x for x in os.listdir(self.levels_path) if x.endswith('.trl'))
        except OSError:
            return {}

        levels = {}
        counts = {}
        for f in files:
            path = os.path.join(self.levels_path, f)
            levels[self._unique_name(taplib.Level.read_title(path), counts)] = path

        return levels

    @staticmethod
    def _unique_name(title, counts) -> str:
        """Disambiguate a repeated title by appending '<N>' (first keeps the bare title)."""
        seen = counts.get(title, 0)
        counts[title] = seen + 1
        return title if seen == 0 else f"{title}<{seen + 1}>"

    def reset(self):
        super().reset()
        self.set_values(None)
        self.level_files = {}
        self.animation   = None
        self.results     = None
        self.key_lane    = {}

    def _playing(self) -> bool:
        return self.animation is not None and self.results is None

    def for_display(self):
        if self.results is not None:
            if self.results.done():
                self.animation = None
                self.results = None
                return super().for_display()
            return self.results
        if self.animation is not None:
            if self.animation.done():
                self.results = self._make_results()
                return self.results
            return self.animation

        return super().for_display()

    def _make_results(self) -> Animation:
        return MarqueeAnimation.fromText(self.animation.results_text(), self.size,
                                         freeze=self.config.results_secs())

    def _load_level(self, name):
        """Resolve a menu name to a Level, or None if it can't be loaded."""
        if name in taplib.BUILTIN_LEVELS:
            return taplib.BUILTIN_LEVELS[name]
        path = self.level_files.get(name)
        if path is None:
            return None
        try:
            return taplib.Level.from_file(path)
        except Exception as e:
            logger.error(f"Failed to load level '{name}': {e}")
            return None

    def _play_key(self, token):
        """Route a key press to the lane it's bound to (no-op if unbound)."""
        if not self._playing():
            return False
        lane = self.key_lane.get(token)
        if lane is None:
            return False
        when = self.watcher.last_pop_time if self.watcher is not None else None
        self.animation.tap(lane, when)
        return True

    def key_enter(self):
        if self.animation is not None or self.results is not None:
            return
        level = self._load_level(self.current_value())
        if level is not None:
            self.key_lane = self.config.key_lane_map()
            self.animation = taplib.TapRevolutionAnimation(level, size=self.size, **self.config.animation_kwargs())

    def key_up(self):
        if not self._play_key('UP'):
            super().key_up()

    def key_down(self):
        if not self._play_key('DOWN'):
            super().key_down()

    def key_left(self):
        self._play_key('LEFT')

    def key_right(self):
        self._play_key('RIGHT')

    def key_char(self, c):
        self._play_key(c)

    def key_esc(self):
        if self._playing():
            self.results = self._make_results()  ## abort -> show score so far
        elif self.results is not None:
            self.animation = None
            self.results = None
        else:
            self.set_done()

    def key_backspace(self):
        self.key_esc()


## Named tuple for one row in the settings list.
## label(draft)->str  get(draft)->value  set(draft,value)->None (None for readonly)
_SettingEntry = collections.namedtuple('_SettingEntry', ['tag', 'type', 'label', 'get', 'set'])

## Seconds the cursor is ON within each 0.5 s blink period.
_CURSOR_ON_SECS = 0.25
## Duration to show validation-failure / save-done flash messages.
_SETTINGS_FLASH_SECS = 1.0


def _build_items(draft):
    """Build the ordered list of _SettingEntry for ``draft``."""
    items = []

    def _add(tag, kind, label_fn, get_fn, set_fn=None):
        items.append(_SettingEntry(tag, kind, label_fn, get_fn, set_fn))

    for lane, key_name in (('left', 'L'), ('right', 'R'), ('up', 'U'), ('down', 'D')):
        _lane = lane
        _add(f'key_{lane}', 'key_binding',
             lambda d, ln=_lane, kn=key_name: f"KEY {kn} {str(d['keys'][ln]).upper()}",
             lambda d, ln=_lane: d['keys'][ln],
             lambda d, v, ln=_lane: d['keys'].__setitem__(ln, v))

    for i, bucket in enumerate(sorted(draft['score_buckets'], key=lambda b: float(b['threshold']))):
        _i = i
        _name = bucket['name']
        _add(f'bucket_{i}', 'bucket',
             lambda d, n=_name: n,
             lambda d, i2=_i: sorted(d['score_buckets'], key=lambda b: float(b['threshold']))[i2],
             None)

    _add('bad_enabled', 'bool',
         lambda d: "BAD " + ("ON" if d['bad_tap'].get('enabled', True) else "OFF"),
         lambda d: d['bad_tap'].get('enabled', True),
         lambda d, v: d['bad_tap'].__setitem__('enabled', v))

    if draft['bad_tap'].get('enabled', True):
        _add('bad_penalty', 'int',
             lambda d: f"BAD -{int(d['bad_tap']['penalty'])}PTS",
             lambda d: d['bad_tap']['penalty'],
             lambda d, v: d['bad_tap'].__setitem__('penalty', v))
        _add('bad_cooldown', 'ms',
             lambda d: f"COOLDOWN {round(float(d['bad_tap']['cooldown']) * 1000)}MS",
             lambda d: d['bad_tap']['cooldown'],
             lambda d, v: d['bad_tap'].__setitem__('cooldown', v))

    _add('grace', 'ms',
         lambda d: f"GRACE {round(float(d['grace']) * 1000)}MS",
         lambda d: d['grace'],
         lambda d, v: d.__setitem__('grace', v))

    _add('flash_secs', 'ms',
         lambda d: f"FLASH {round(float(d['flash_secs']) * 1000)}MS",
         lambda d: d['flash_secs'],
         lambda d, v: d.__setitem__('flash_secs', v))

    _add('judge_flash', 'bool',
         lambda d: "JUDGE " + ("ON" if d['judge_flash'] else "OFF"),
         lambda d: d['judge_flash'],
         lambda d, v: d.__setitem__('judge_flash', v))

    _add('results_secs', 'int',
         lambda d: f"RESULTS {int(d['results_secs'])}S",
         lambda d: d['results_secs'],
         lambda d, v: d.__setitem__('results_secs', v))

    ## Read-only entries
    _add('score_width', 'readonly',
         lambda d: f"SCORE WIDTH {int(d['score_width'])}",
         lambda d: d['score_width'])

    _add('hit_flash', 'readonly',
         lambda d: f"HIT FLASH {d['hit_flash']['frame_secs']*1000:.0f}MS",
         lambda d: d['hit_flash'])

    return items


class TapRevolutionSettingsItem(MenuItem):
    """Editable, scrollable settings for Tap Revolution.

    Implements a state machine (browse / edit / bucket / bucket_edit /
    save_confirm) entirely within one MenuItem so the Navigator's back/done
    machinery is not involved until the user chooses to save or discard.
    """
    def __init__(self, config, **kwargs):
        super().__init__("Settings", **kwargs)
        self.config = config
        self._reset_state()

    ## ------------------------------------------------------------------ ##
    ## Lifecycle                                                            ##
    ## ------------------------------------------------------------------ ##

    def activate(self):
        self._draft = copy.deepcopy(self.config.settings)
        self._rebuild_items()

    def reset(self):
        super().reset()
        self._reset_state()

    def _reset_state(self):
        self.state         = 'browse'
        self._draft        = copy.deepcopy(self.config.settings)
        self._items        = []
        self._idx          = 0
        self._bucket_idx   = 0
        self._bucket_sub   = 0
        self._bucket_orig  = None
        self._edit_buffer  = ''
        self._edit_bool    = False
        self._edit_orig    = None
        self._flash_msg    = ''
        self._flash_until  = 0.0

    def _rebuild_items(self):
        self._items = _build_items(self._draft)
        self._idx = max(0, min(self._idx, len(self._items) - 1))

    ## ------------------------------------------------------------------ ##
    ## Display                                                              ##
    ## ------------------------------------------------------------------ ##

    def for_display(self) -> str:
        if self.state == 'browse':
            return self._display_browse()
        if self.state == 'edit':
            return self._display_edit()
        if self.state == 'bucket':
            return self._display_bucket()
        if self.state == 'bucket_edit':
            return self._display_bucket_edit()
        if self.state == 'save_confirm':
            return "SAVE Y/N"
        return ''

    def _cursor(self, s) -> str:
        """Append a blinking underlined-space cursor to s."""
        if time.time() % 0.5 < _CURSOR_ON_SECS:
            return s + ' !'
        return s

    def _display_browse(self) -> str:
        if self._flash_msg and time.time() < self._flash_until:
            return self._flash_msg
        if not self._items:
            return "No Settings"
        return self._items[self._idx].label(self._draft)

    def _display_edit(self) -> str:
        entry = self._items[self._idx]
        if entry.type == 'key_binding':
            return "PRESS KEY"
        if entry.type == 'bool':
            val = "ON" if self._edit_bool else "OFF"
            return self._cursor(val)
        ## numeric (ms or int): show label prefix + buffer
        label = entry.label(self._draft)
        ## Strip trailing unit suffix to show raw digits during edit
        buf = self._edit_buffer or ''
        return self._cursor(f"{self._edit_prefix(entry)} {buf}")

    def _edit_prefix(self, entry) -> str:
        """Short label to show before the edit buffer."""
        label = entry.label(self._draft)
        ## Drop trailing digits/units (keep the descriptive word part)
        for sep in (' ', '-'):
            if sep in label:
                return label.rsplit(sep, 1)[0]
        return label

    def _display_bucket(self) -> str:
        bucket = self._current_bucket()
        if self._bucket_sub == 0:
            return f"THRESH {round(float(bucket['threshold']) * 1000)}MS"
        return f"POINTS {int(bucket['points'])}"

    def _display_bucket_edit(self) -> str:
        bucket = self._current_bucket()
        if self._bucket_sub == 0:
            return self._cursor(f"THRESH {self._edit_buffer}")
        return self._cursor(f"POINTS {self._edit_buffer}")

    ## ------------------------------------------------------------------ ##
    ## Key dispatch                                                         ##
    ## ------------------------------------------------------------------ ##

    def key_enter(self):
        if self.state == 'browse':
            self._browse_enter()
        elif self.state == 'edit':
            self._edit_commit()
        elif self.state == 'bucket':
            self._bucket_enter()
        elif self.state == 'bucket_edit':
            self._bucket_edit_commit()

    def key_esc(self):
        if self.state == 'browse':
            self.state = 'save_confirm'
        elif self.state == 'edit':
            self.state = 'browse'
        elif self.state == 'bucket':
            self._bucket_exit()
        elif self.state == 'bucket_edit':
            self.state = 'bucket'
            self._edit_buffer = ''
        elif self.state == 'save_confirm':
            self.set_done()

    def key_backspace(self):
        self.key_esc()

    def key_up(self):
        if self.state == 'browse':
            self._idx = max(0, self._idx - 1)
        elif self.state == 'edit':
            if self._items[self._idx].type == 'key_binding':
                self._key_binding_commit('up')
            else:
                self._edit_toggle_bool()
        elif self.state == 'bucket':
            self._bucket_sub = max(0, self._bucket_sub - 1)

    def key_down(self):
        if self.state == 'browse':
            self._idx = min(len(self._items) - 1, self._idx + 1)
        elif self.state == 'edit':
            if self._items[self._idx].type == 'key_binding':
                self._key_binding_commit('down')
            else:
                self._edit_toggle_bool()
        elif self.state == 'bucket':
            self._bucket_sub = min(1, self._bucket_sub + 1)

    def key_left(self):
        if self.state == 'edit':
            if self._items[self._idx].type == 'key_binding':
                self._key_binding_commit('left')
            else:
                self._edit_toggle_bool()

    def key_right(self):
        if self.state == 'edit':
            if self._items[self._idx].type == 'key_binding':
                self._key_binding_commit('right')
            else:
                self._edit_toggle_bool()

    def key_char(self, c):
        if self.state == 'save_confirm':
            self._save_confirm_char(c)
        elif self.state == 'edit':
            self._edit_char(c)
        elif self.state == 'bucket_edit':
            self._bucket_edit_char(c)

    ## ------------------------------------------------------------------ ##
    ## Browse helpers                                                       ##
    ## ------------------------------------------------------------------ ##

    def _browse_enter(self):
        if not self._items:
            return
        entry = self._items[self._idx]
        if entry.type == 'readonly':
            return
        if entry.type == 'bucket':
            self._enter_bucket()
            return
        ## Editable scalar: enter edit mode
        self._edit_orig = entry.get(self._draft)
        self._edit_buffer = ''
        if entry.type == 'bool':
            self._edit_bool = bool(entry.get(self._draft))
        self.state = 'edit'

    ## ------------------------------------------------------------------ ##
    ## Edit helpers (scalar)                                                ##
    ## ------------------------------------------------------------------ ##

    def _edit_toggle_bool(self):
        if self._items[self._idx].type == 'bool':
            self._edit_bool = not self._edit_bool

    def _edit_char(self, c):
        entry = self._items[self._idx]
        if entry.type == 'key_binding':
            self._key_binding_commit(c)
        elif c.isdigit() and len(self._edit_buffer) < 5:
            self._edit_buffer += c

    def _edit_commit(self):
        entry = self._items[self._idx]
        if entry.type == 'bool':
            entry.set(self._draft, self._edit_bool)
            self._rebuild_items()
        elif entry.type in ('ms', 'int'):
            if self._edit_buffer:
                raw = int(self._edit_buffer)
                value = raw / 1000.0 if entry.type == 'ms' else raw
                entry.set(self._draft, value)
        self._edit_buffer = ''
        self.state = 'browse'

    def _key_binding_commit(self, value):
        """Immediately commit an arrow-name or char as a key binding."""
        entry = self._items[self._idx]
        entry.set(self._draft, value)
        self._edit_buffer = ''
        self.state = 'browse'

    ## ------------------------------------------------------------------ ##
    ## Bucket helpers                                                       ##
    ## ------------------------------------------------------------------ ##

    def _current_bucket(self):
        ordered = sorted(self._draft['score_buckets'], key=lambda b: float(b['threshold']))
        return ordered[self._bucket_idx]

    def _current_bucket_global_idx(self):
        """Index into the original (unordered) score_buckets list."""
        ordered = sorted(self._draft['score_buckets'], key=lambda b: float(b['threshold']))
        target = ordered[self._bucket_idx]
        for i, b in enumerate(self._draft['score_buckets']):
            if b is target:
                return i
        return 0

    def _enter_bucket(self):
        ## Which bucket position in the _items list is at _idx?
        bucket_entries = [e for e in self._items if e.type == 'bucket']
        pos = next((i for i, e in enumerate(bucket_entries) if e.tag == self._items[self._idx].tag), 0)
        self._bucket_idx = pos
        self._bucket_sub = 0
        self._bucket_orig = copy.deepcopy(self._current_bucket())
        self.state = 'bucket'

    def _bucket_enter(self):
        bucket = self._current_bucket()
        self._edit_buffer = str(round(float(bucket['threshold']) * 1000)) if self._bucket_sub == 0 else str(int(bucket['points']))
        self.state = 'bucket_edit'

    def _bucket_edit_commit(self):
        if not self._edit_buffer:
            self.state = 'bucket'
            return
        bucket = self._current_bucket()
        raw = int(self._edit_buffer)
        if self._bucket_sub == 0:
            bucket['threshold'] = raw / 1000.0
        else:
            bucket['points'] = raw
        self._edit_buffer = ''
        self.state = 'bucket'

    def _bucket_edit_char(self, c):
        if c.isdigit() and len(self._edit_buffer) < 5:
            self._edit_buffer += c

    def _bucket_exit(self):
        if not _TRConfig.validate_buckets(self._draft['score_buckets']):
            ## Revert this bucket and flash a warning
            gi = self._current_bucket_global_idx()
            if self._bucket_orig is not None:
                self._draft['score_buckets'][gi] = self._bucket_orig
            self._flash_msg = "INVALID"
            self._flash_until = time.time() + _SETTINGS_FLASH_SECS
        self.state = 'browse'

    ## ------------------------------------------------------------------ ##
    ## Save-confirm helpers                                                 ##
    ## ------------------------------------------------------------------ ##

    def _save_confirm_char(self, c):
        if c.lower() == 'y':
            self.config.settings = copy.deepcopy(self._draft)
            self.config.save()
            self.set_done()
        elif c.lower() == 'n':
            self.set_done()


class ResetSettingsItem(MenuItem):
    """Restore Tap Revolution settings to the defaults file, behind a y/n confirm."""
    def __init__(self, config, **kwargs):
        super().__init__("Reset Settings", **kwargs)
        self.config = config
        self.state  = 'confirm'

    def activate(self):
        self.state = 'confirm'

    def reset(self):
        super().reset()
        self.state = 'confirm'

    def for_display(self) -> str:
        return "Settings reset" if self.state == 'done' else "Reset Y/N"

    def key_char(self, c):
        if self.state == 'done':
            self.set_done()
        elif c.lower() == 'y':
            self.config.reset()
            self.state = 'done'
        elif c.lower() == 'n':
            self.set_done()

    def key_enter(self):
        if self.state == 'done':
            self.set_done()

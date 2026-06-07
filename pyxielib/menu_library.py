import re
import os
import subprocess
import threading

from pyxielib.navigator import DelayedCommandItem, ListItem, Menu, MenuItem, MsgItem, SubcommandItem
from pyxielib.wifi_controller import WiFiController
from pyxielib.animation import Animation
from pyxielib.animation_file import FileAnimation


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

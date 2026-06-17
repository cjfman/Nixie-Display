import json
import logging
import re
import os
import socket
import subprocess
import threading
import time
from collections import deque
from typing import Dict, List, Optional

from pyxielib.audio_controller import AudioController, BluetoothDevice
from pyxielib.navigator import CycleItem, DelayedCommandItem, ListItem, Menu, MenuItem, MsgItem, SubcommandItem
from pyxielib.wifi_controller import WiFiController
from pyxielib.wifi_ap_controller import WiFiAPConfig, WiFiAPController
from pyxielib.animation import Animation, LoopedFullFrameAnimation, MarqueeAnimation
from pyxielib.frames import FullFrame, HexFrame, TextFrame
from pyxielib.decoder import isPrintable
from pyxielib.animation_file import FileAnimation
from pyxielib.animation_library import ProgressSpinner

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

    def __init__(self, size=16, **kwargs):
        super().__init__("Git Status", **kwargs)
        self.size = size
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

    def git_status(self):
        """Build the list of status lines, or a single line on failure."""
        commit = self._git('rev-parse', 'HEAD')
        if commit is None:
            return ["Not a git repo"]
        branch = self._git('symbolic-ref', '--short', '-q', 'HEAD')
        return [branch or "Detached HEAD", commit[:self.size], self._upstream_status()]

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
        ip = SystemInfoItem._run('sh', '-c', "ip route get 8.8.8.8 2>/dev/null | head -1 | cut -d' ' -f7")
        if ip and re.match(r'^\d{1,3}(\.\d{1,3}){3}$', ip):
            items.append(ip)
        else:
            items.append("IP: N/A")
        items.append("OS: " + SystemInfoItem._read_os_release())
        kernel = SystemInfoItem._run('uname', '-r') or "N/A"
        items.append("Kernel: " + kernel.split('-')[0])
        hw = SystemInfoItem._hardware_model()
        items.append("HW: " + (SystemInfoItem._shorten_hw(hw) if hw else "N/A"))
        ver = SystemInfoItem._run('python3', '--version')
        items.append(ver or "Python: N/A")
        uptime = SystemInfoItem._run('uptime', '-p')
        items.append("Up: " + (SystemInfoItem._shorten_uptime(uptime) if uptime else "N/A"))
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
        """Return a compact OS name: strips GNU/Linux and parentheticals."""
        try:
            with open('/etc/os-release') as f:
                for line in f:
                    if line.startswith('PRETTY_NAME='):
                        name = line.split('=', 1)[1].strip().strip('"')
                        name = re.sub(r'\s*\(.*?\)', '', name)
                        name = name.replace('GNU/Linux', '')
                        return ' '.join(name.split())[:12]
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
    def _shorten_hw(model: str) -> str:
        """Abbreviate e.g. 'Raspberry Pi 4 Model B Rev 1.4' → 'RPi 4B'."""
        model = model.replace('Raspberry Pi', 'RPi')
        model = re.sub(r'\s*Rev\s+[\d.]+', '', model)
        model = model.replace(' Model ', '')
        return ' '.join(model.split())

    @staticmethod
    def _shorten_uptime(uptime: str) -> str:
        """Compact 'up 3 days, 4 hours, 22 minutes' → '3d 4h 22m'."""
        parts = []
        m = re.search(r'(\d+)\s+day', uptime)
        if m:
            parts.append(m.group(1) + 'd')
        m = re.search(r'(\d+)\s+hour', uptime)
        if m:
            parts.append(m.group(1) + 'h')
        m = re.search(r'(\d+)\s+min', uptime)
        if m:
            parts.append(m.group(1) + 'm')
        return ' '.join(parts) if parts else uptime

    @staticmethod
    def _cpu_temp() -> str:
        try:
            raw = open('/sys/class/thermal/thermal_zone0/temp').read().strip()
            return f"{int(raw) / 1000:.1f}C"
        except OSError:
            return "N/A"


class TextBodyItem(MenuItem):
    """Reusable scrollable viewer for a multi-line body of text.

    Up/Down step between lines; Left/Right pan a long line horizontally. A
    flashing '<'/'>' edge indicator shows there is more text to the side (as in
    pyprint's ``run_interactive``), and a flashing up/down indicator shows there
    are more lines above/below. Both blink together at 1 Hz.

    ``for_display`` returns an ``Animation`` (not a str): the indicators use raw
    bitmaps, which ``MarqueeAnimation.fromText`` would mangle. The animation is
    cached and the *same* object is returned every poll so the blink loop keeps
    playing; a key that changes the view clears the cache so the next poll
    rebuilds (and thus restarts) it.
    """
    ## 14-segment bitmaps for the up/down chevrons (see decoder.py / Tap Revolution).
    _UP_GLYPH   = 0x1400   ## '^'
    _DOWN_GLYPH = 0x0140   ## '\ /'
    _UNDERLINE  = 0x4000   ## plain underline, shown for unprintable characters

    def __init__(self, name, lines=None, *, size=16, unprintable_code=_UNDERLINE, **kwargs):
        super().__init__(name, **kwargs)
        self.size             = size
        self.unprintable_code = unprintable_code
        self.lines            = list(lines) if lines else [""]
        self.line             = 0
        self.offset           = 0
        self._animation       = None

    def set_lines(self, lines, line=0):
        """Load a new body of text and return to the top."""
        self.lines      = list(lines) if lines else [""]
        self.line       = 0
        self.offset     = 0
        self._animation = None
        size = len(self.lines)
        if line > 0:
            self.line = min(line, size - 1)
        elif line < 0:
            self.line = max(0, size + line)

    def reset(self):
        super().reset()
        self.line       = 0
        self.offset     = 0
        self._animation = None

    def _current(self) -> str:
        return self.lines[self.line]

    def key_left(self):
        if self.offset > 0:
            self.offset    -= 1
            self._animation = None

    def key_right(self):
        if self.offset + self.size < len(self._current()):
            self.offset    += 1
            self._animation = None

    def key_up(self):
        if self.line > 0:
            self.line      -= 1
            self.offset     = 0
            self._animation = None

    def key_down(self):
        if self.line + 1 < len(self.lines):
            self.line      += 1
            self.offset     = 0
            self._animation = None

    def key_enter(self):
        """Page to the next line (pyprint parity). ESC/BACKSPACE exit."""
        self.key_down()

    def for_display(self) -> Animation:
        if self._animation is None:
            self._animation = self._build()
        return self._animation

    def _char_frame(self, c):
        """Frame for one character: the glyph if printable, else the replacement code."""
        if isPrintable(c):
            return TextFrame(c)
        return HexFrame(self.unprintable_code)

    @classmethod
    def _ud_glyph(cls, has_up, has_down) -> Optional[int]:
        """Combined up/down indicator bitmap, or None when neither applies."""
        if has_up and has_down:
            return cls._UP_GLYPH | cls._DOWN_GLYPH
        if has_up:
            return cls._UP_GLYPH
        if has_down:
            return cls._DOWN_GLYPH
        return None

    def _build(self) -> Animation:
        """Build the two-frame (indicators on/off) blink animation."""
        text   = self._current()
        window = list(text[self.offset:self.offset + self.size])
        window += [' '] * (self.size - len(window))

        ## One frame per char (not textToFrames) so ':'/'!' render literally
        ## instead of being interpreted as colon/underline commands; unprintable
        ## characters become a plain underline rather than a blank NOCODE tube.
        off_frames = [self._char_frame(c) for c in window]
        on_frames  = [self._char_frame(c) for c in window]

        has_left  = self.offset > 0
        has_right = self.offset + self.size < len(text)
        if has_left:
            on_frames[0] = TextFrame('<')
        if has_right:
            on_frames[-1] = TextFrame('>')

        ud = self._ud_glyph(self.line > 0, self.line + 1 < len(self.lines))
        if ud is not None:
            ## Sit just left of the '>' when it is shown, else take the last tube.
            on_frames[-2 if has_right else -1] = HexFrame(ud)

        frames = [FullFrame(on_frames), FullFrame(off_frames)]
        return LoopedFullFrameAnimation.makeTimed(frames, delay=0.5)


class LogViewerItem(TextBodyItem):
    """Browse a log file in the menu, most-recent line first.

    Each line's ``$DATE $LEVEL $MODULE: `` preamble is stripped and replaced
    with a four-character contraction of the log level, so the message itself
    fits on the display instead of being pushed off by the timestamp.
    """
    ## Matches the logging format: '%(asctime)s %(levelname)-8s %(name)s: %(message)s'
    _PREAMBLE = re.compile(
        r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} (\w+)\s+\S+: (.*)$')
    _LEVELS = {
        'INFO':    'INFO',
        'WARN':    'WARN',
        'WARNING': 'WARN',
        'ERROR':   'ERRR',
        'DEBUG':   'DBUG',
        'VERBOSE': 'VBOS',
        'TRACE':   'TRCE',
    }

    _positions_file: str = os.path.expanduser('~/.nixie/nixie_log_positions.json')
    _positions: Optional[Dict[str, int]] = None  ## class-level, keyed by expanded path

    @classmethod
    def _load_positions(cls):
        if cls._positions is None:
            try:
                with open(cls._positions_file) as f:
                    cls._positions = json.load(f)
            except (OSError, json.JSONDecodeError):
                cls._positions = {}

    @classmethod
    def _flush_positions(cls):
        try:
            os.makedirs(os.path.dirname(cls._positions_file), exist_ok=True)
            with open(cls._positions_file, 'w') as f:
                json.dump(cls._positions, f)
        except OSError:
            logger.warning("Could not save log positions to %s", cls._positions_file)

    def __init__(self, path, *, tail=200, **kwargs):
        super().__init__("Logs", **kwargs)
        self.path = os.path.expanduser(path)
        self.tail = tail

    def reset(self):
        n = len(self.lines)
        self._load_positions()
        self._positions[self.path] = max(0, n - 1 - self.line)
        self._flush_positions()
        super().reset()

    def activate(self):
        lines = self._read()
        n = len(lines)
        self._load_positions()
        saved = self._positions.get(self.path, 0)
        ## Restore same "age" depth; fall back to oldest if tail is now shorter.
        target = n - 1 - saved if saved < n else 0
        self.set_lines(lines, target)

    @classmethod
    def _strip_preamble(cls, line):
        """Replace the date/level/module preamble with a contracted level."""
        match = cls._PREAMBLE.match(line)
        if match is None:
            return line   ## continuation lines (e.g. tracebacks) pass through
        level, message = match.group(1), match.group(2)
        abbr = cls._LEVELS.get(level.upper(), level.upper()[:4])
        return f"{abbr} {message}"

    def _read(self) -> List[str]:
        try:
            with open(os.path.expanduser(self.path)) as f:
                lines = [self._strip_preamble(line.rstrip('\n'))
                         for line in deque(f, maxlen=self.tail)]
        except OSError:
            return ["No log file"]
        if not lines:
            return ["(empty log)"]
        return lines


class RebootItem(DelayedCommandItem):
    def __init__(self, **kwargs):
        super().__init__("Reboot", "sudo reboot", running_msg="Rebooting...", **kwargs)


class ShutdownItem(DelayedCommandItem):
    def __init__(self, **kwargs):
        super().__init__("Shutdown", "sudo halt", running_msg="Shutting down...", **kwargs)


class ExitItem(MenuItem):
    def activate(self):
        logger.info("Exit selected; stopping program")
        raise KeyboardInterrupt()


class SleepItem(MenuItem):
    def __init__(self, controller, **kwargs):
        super().__init__("Sleep Display", **kwargs)
        self.controller = controller

    def activate(self):
        logger.info("Display sleep")
        self.controller.disable()

    def key_char(self, c):
        ## pylint: disable=unused-argument
        self.set_done()

    def key_arrow(self, d):
        ## pylint: disable=unused-argument
        self.set_done()

    def set_done(self):
        logger.info("Display wake")
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
            logger.info("Saved WiFi network '%s' (%s)", self.ssid,
                        "ok" if success else "failed")
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
        self._target = None

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
        self._target = None

    def poll(self):
        if self.state != 'connecting':
            return

        success = self.wifi.poll()
        if success is not None:
            self.state = 'success' if success else 'failed'
            logger.info("WiFi network '%s' %s", self._target,
                        "connected" if success else "connection failed")

    def select(self):
        self._target = self.current_value()
        logger.info("Connecting to WiFi network '%s'", self._target)
        self.wifi.select_network(self._target, blocking=False)

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


def _interface_ip(iface) -> Optional[str]:
    """Return the first IPv4 address on <iface>, or None if absent/unset."""
    try:
        proc = subprocess.run(
            ['ip', '-4', '-o', 'addr', 'show', iface],
            capture_output=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    match = re.search(r'\binet\s+(\d{1,3}(?:\.\d{1,3}){3})', proc.stdout.decode(errors='replace'))
    if match:
        return match.group(1)

    return None


class WiFiAPItem(MenuItem):
    """Toggle the on-demand WiFi access point on/off, with a y/n confirm.

    Enabling the AP replaces client WiFi (single radio), so Enter prompts for
    confirmation before switching. Mirrors the WiFiSelectItem state machine.
    """
    def __init__(self, controller, config, **kwargs):
        super().__init__("WiFi AP", **kwargs)
        self.ctrl = controller
        self.config = config
        self.state = 'idle'
        self._was_on = False
        self._ok = False

    def for_display(self) -> str:
        if self.state == 'idle':
            return self._status_text()
        if self.state == 'confirm':
            return "Disable AP?[y/n]" if self._was_on else "Enable AP?[y/n]"
        if self.state == 'working':
            return "Stopping..." if self._was_on else "Starting..."
        if self.state == 'result':
            if self._ok:
                return "AP OFF" if self._was_on else "AP ON"
            return "Failed"

        return "AP Err"

    def _status_text(self) -> str:
        status = "ON" if self.ctrl.status() else "OFF"
        return f"AP: {status}"

    def reset(self):
        super().reset()
        self.state = 'idle'

    def key_enter(self):
        if self.state == 'idle':
            self._was_on = self.ctrl.status()
            self.state = 'confirm'
        elif self.state == 'result':
            self.set_done()

    def key_char(self, c):
        if self.state != 'confirm':
            return

        c = c.lower()
        if c == 'y':
            self.state = 'working'
            if self._was_on:
                self._ok = self.ctrl.disable()
            else:
                self._ok = self.ctrl.enable(self.config.ssid, self.config.password)
            logger.info("WiFi AP %s %s", "disable" if self._was_on else "enable",
                        "succeeded" if self._ok else "failed")
            self.state = 'result'
        elif c == 'n':
            self.state = 'idle'


class SSHAccessMenu(Menu):
    """Reachability info for SSHing into the Pi: hostname + USB/WiFi addresses."""
    _marker_file = os.path.expanduser('~/.nixie/usb_gadget_enabled')

    def __init__(self, wifi_ap_config=None):
        super().__init__("SSH Access")
        self.wifi = WiFiController('wlan0', sudo=True)
        self.add_submenu(MsgItem("SSH Host", self._ssh_host))
        self.add_submenu(MsgItem("USB", self._usb_status))
        self.add_submenu(MsgItem("WiFi", self._wifi_status))
        self.add_submenu(WiFiAPItem(WiFiAPController('wlan0'), wifi_ap_config or WiFiAPConfig()))

    def activate(self):
        super().activate()
        self.wifi.load(force=True)

    @staticmethod
    def _ssh_host() -> str:
        host = socket.gethostname().rstrip('.')
        if host.endswith('.local'):
            return host

        return f"{host}.local"

    def _usb_status(self) -> str:
        if not os.path.exists(self._marker_file):
            return "Not available"

        return _interface_ip("usb0") or "Connect USB"

    def _wifi_status(self) -> str:
        return self.wifi.ip_address() or "Not connected"


class WiFiMenu(Menu):
    def __init__(self, wifi_ap_config=None):
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
        self.add_submenu(WiFiAPItem(WiFiAPController('wlan0'), wifi_ap_config or WiFiAPConfig()))

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


## Tap Revolution menu classes live in tap_revolution_menu.py.


class AudioSelectItem(ListItem):
    def __init__(self, audio: AudioController, **kwargs):
        super().__init__("Audio Select", **kwargs)
        self.audio = audio
        self.state = 'select'
        self._sinks = []

    def activate(self):
        self._sinks = self.audio.list_sinks()
        if self._sinks:
            self.set_values([s.description[:16] for s in self._sinks])
        elif not self.audio.server_running():
            ## Distinguish "can't reach the audio server" from a real empty list
            ## so it's clear the menu found nothing because pactl can't connect.
            self.set_values(["No audio server"])
        else:
            self.set_values(["No outputs"])

    def for_display(self) -> str:
        if self.state == 'select':
            return super().for_display()
        if self.state == 'confirm':
            return "Set output [y/n]?"
        if self.state == 'done':
            return "Output set"
        if self.state == 'failed':
            return "Set failed"
        return f"Error: {self.state}"

    def key_enter(self):
        if self.state == 'select':
            if self._sinks:
                self.state = 'confirm'
            else:
                self.reset()
                self.set_done()
        elif self.state in ('done', 'failed'):
            self.reset()
            self.set_done()

    def key_char(self, c):
        if self.state != 'confirm':
            return
        c = c.lower()
        if c == 'y':
            sink = self._sinks[self.idx]
            success = self.audio.set_default_sink(sink.name)
            logger.info("Audio output set to '%s' (%s)", sink.description,
                        "ok" if success else "failed")
            self.state = 'done' if success else 'failed'
        elif c == 'n':
            self.state = 'select'

    def reset(self):
        super().reset()
        self._sinks = []
        self.state = 'select'
        self.set_values(None)


class BTAddItem(ListItem):
    ## Seconds the paired/failed result screen lingers before auto-dismissing.
    _RESULT_SECS = 5.0

    def __init__(self, audio: AudioController, **kwargs):
        super().__init__("BT Add", **kwargs)
        self.audio = audio
        self.state = None
        self._devices = []
        self._paired_macs = set()
        self._scan_spinner = None
        self._pair_spinner = None
        self._result_until = 0.0
        self._pairing = None

    def activate(self):
        self._paired_macs = {d.mac for d in self.audio.list_paired_devices()}
        self.audio.scan_start(timeout=10)
        self._scan_spinner = None
        self._pair_spinner = None
        self._result_until = 0.0
        self.state = 'scanning'

    def _spinner(self, attr, label) -> ProgressSpinner:
        """Return the cached spinner, rebuilding it once it finishes a fill cycle.

        ProgressSpinner is a one-shot animation, so recreating it on done()
        both loops it visually and lets the scheduler re-poll us each cycle
        (see ProgressSpinner / poll())."""
        spinner = getattr(self, attr)
        if spinner is None or spinner.done():
            spinner = ProgressSpinner(label)
            setattr(self, attr, spinner)
        return spinner

    def for_display(self):
        self.poll()
        if self.state == 'scanning':
            return self._spinner('_scan_spinner', "Scanning BT")
        if self.state == 'select':
            return super().for_display()
        if self.state == 'confirm':
            return "Pair [y/n]?"
        if self.state == 'pairing':
            return self._spinner('_pair_spinner', "Pairing")
        if self.state in ('paired', 'failed'):
            if self._result_until and time.time() >= self._result_until:
                self.set_done()
            return "Paired" if self.state == 'paired' else "Failed"
        return f"Error: {self.state}"

    def poll(self):
        if self.state == 'scanning':
            result = self.audio.scan_poll()
            if result is not None:
                new_devs = [d for d in result if d.mac not in self._paired_macs and d.named]
                self._devices = new_devs
                if new_devs:
                    self.set_values([d.name[:16] for d in new_devs])
                else:
                    self.set_values(["No new devices"])
                self.state = 'select'
        elif self.state == 'pairing':
            result = self.audio.poll_pair()
            if result is True:
                self.state = 'paired'
                self._result_until = time.time() + self._RESULT_SECS
                logger.info("Bluetooth device '%s' paired", self._pairing)
            elif result is False:
                self.state = 'failed'
                self._result_until = time.time() + self._RESULT_SECS
                logger.info("Bluetooth pairing with '%s' failed", self._pairing)

    def key_enter(self):
        if self.state == 'select':
            if self._devices and self.idx < len(self._devices):
                self.state = 'confirm'
            else:
                self.reset()
                self.set_done()
        elif self.state in ('paired', 'failed'):
            self.reset()
            self.set_done()

    def key_char(self, c):
        if self.state != 'confirm':
            return
        c = c.lower()
        if c == 'y':
            device = self._devices[self.idx]
            self._pairing = device.name
            logger.info("Pairing Bluetooth device '%s' (%s)", device.name, device.mac)
            self.audio.pair_and_connect_async(device.mac)
            self.state = 'pairing'
        elif c == 'n':
            self.state = 'select'

    def reset(self):
        super().reset()
        self.audio.scan_cancel()
        self._devices = []
        self._paired_macs = set()
        self._scan_spinner = None
        self._pair_spinner = None
        self._result_until = 0.0
        self._pairing = None
        self.state = None
        self.set_values(None)


class BTRemoveItem(ListItem):
    def __init__(self, audio: AudioController, **kwargs):
        super().__init__("BT Remove", **kwargs)
        self.audio = audio
        self.state = 'select'
        self._devices = []

    def activate(self):
        self._devices = self.audio.list_paired_devices()
        if self._devices:
            self.set_values([d.name[:16] for d in self._devices])
        else:
            self.set_values(["No paired devices"])

    def for_display(self) -> str:
        if self.state == 'select':
            return super().for_display()
        if self.state == 'confirm':
            return "Remove? [y/n]"
        if self.state == 'removing':
            return "Removing..."
        if self.state == 'removed':
            return "Removed"
        if self.state == 'failed':
            return "Failed"
        return f"Error: {self.state}"

    def key_enter(self):
        if self.state == 'select':
            if self._devices and self.idx < len(self._devices):
                self.state = 'confirm'
            else:
                self.reset()
                self.set_done()
        elif self.state in ('removed', 'failed'):
            self.reset()
            self.set_done()

    def key_char(self, c):
        if self.state != 'confirm':
            return
        c = c.lower()
        if c == 'y':
            device = self._devices[self.idx]
            self.state = 'removing'
            success = self.audio.remove_device(device.mac)
            logger.info("Bluetooth device '%s' (%s) removal %s", device.name, device.mac,
                        "succeeded" if success else "failed")
            self.state = 'removed' if success else 'failed'
        elif c == 'n':
            self.state = 'select'

    def reset(self):
        super().reset()
        self._devices = []
        self.state = 'select'
        self.set_values(None)


class AudioTestItem(MenuItem):
    """Plays a test sound when entered; shows 'Testing' until playback finishes,
    then pops back so Enter on the same item re-runs the test.
    Shows 'Test failed' for a brief flash if playback fails."""
    _FLASH_SECS = 1.5

    def __init__(self, audio: AudioController, test_sound='audio-test-signal', **kwargs):
        super().__init__("Test Audio", **kwargs)
        self.audio = audio
        self.test_sound = test_sound
        self._testing = False
        self._flash_until = 0.0

    def activate(self):
        self.audio.play_test_sound_async(self.test_sound)
        self._testing = True
        self._flash_until = 0.0

    def for_display(self) -> str:
        if self._testing:
            result = self.audio.poll_test()
            if result is False:
                self._testing = False
                self._flash_until = time.time() + self._FLASH_SECS
            elif result is True:
                self._testing = False
                self.set_done()
        if self._flash_until:
            if time.time() < self._flash_until:
                return "Test failed"
            self.set_done()
        return "Testing"

    def reset(self):
        super().reset()
        self.audio.stop_test()
        self._testing = False
        self._flash_until = 0.0

    def key_enter(self):
        pass  ## handled by Navigator.enter() → activate()

    def key_esc(self):
        self.audio.stop_test()
        self._testing = False
        self._flash_until = 0.0
        self.set_done()

    def key_backspace(self):
        self.key_esc()


class AudioDiagItem(TextBodyItem):
    """Read-only audio diagnostics: whether pactl can reach the audio server
    (and the XDG_RUNTIME_DIR it needs) plus the detected sinks. Lets you check
    from the display when Select Output is unexpectedly empty. Rebuilt on each
    activate() so it always reflects the current state."""
    def __init__(self, audio: AudioController, *, size=16, **kwargs):
        super().__init__("Audio Diagnosis", size=size, **kwargs)
        self.audio = audio

    def activate(self):
        self.set_lines(self.audio.diagnostics())


class AudioMenu(Menu):
    def __init__(self, test_sound='audio-test-signal', size=16):
        super().__init__("Audio Settings")
        self.audio = AudioController()
        current = lambda: self.audio.get_default_sink_description() or "Unknown"
        self.add_submenu(MsgItem("View Current", current))
        self.add_submenu(AudioSelectItem(self.audio, display_name="Select Output"))
        self.add_submenu(CycleItem(
            "Mute",
            [(False, 'OFF'), (True, 'ON')],
            get_fn=self.audio.is_muted,
            set_fn=self.audio.set_mute,
        ))
        self.add_submenu(BTAddItem(self.audio, display_name="Add Bluetooth"))
        self.add_submenu(BTRemoveItem(self.audio, display_name="Remove Bluetooth"))
        self.add_submenu(AudioTestItem(self.audio, test_sound=test_sound, display_name="Test Audio"))
        self.add_submenu(AudioDiagItem(self.audio, size=size, display_name="Audio Diagnosis"))

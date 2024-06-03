import re
import subprocess

from pyxielib.navigator import ListItem, Menu, MsgItem, SubcommandItem
from pyxielib.wifi_controller import WiFiController


class IpItem(SubcommandItem):
    def __init__(self):
        super().__init__("Show IP Address", ['ip', 'route', 'list', 'default'])

    def run(self) -> str:
        output = super().run()
        match = re.match(r"default via (\S+)", output)
        if match:
            return match.groups()[0]

        return "No IP Address"


class WiFiScanItem(ListItem):
    def __init__(self, device='wlan0'):
        super().__init__("WiFi Networks")
        self.device    = device
        self.proc      = None
        self.started   = False
        self.completed = False
        self.failed    = False

    def reset(self):
        super().reset()
        self.proc      = None
        self.started   = False
        self.completed = False
        self.failed    = False

    def for_display(self) -> str:
        if self.completed:
            return super().for_display()

        self.poll()
        if not self.started:
            return "Scan not started"
        if self.failed:
            return "Scan failed"
        if not self.completed:
            return "Scanning..."

        return super().for_display()

    def on_active(self):
        self.run()
        self.started = True

    def run(self):
        cmd = ['sudo', 'iwlist', self.device, 'scan']
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)

    def poll(self):
        ret = self.proc.poll()
        if ret is None:
            return
        if ret != 0:
            self.failed = True

        networks = []
        for line in self.proc.stdout:
            match = re.search(r'ESSID:"(.+)"', line.decode('utf8'))
            if match:
                networks.append(match.groups()[0])

        header = f"Found {len(networks)} SSIDs"
        self.set_values([header] + networks)
        self.completed = True


class WiFiSelectItem(ListItem):
    def __init__(self, wifi, **kwargs):
        super().__init__("WiFi Select", wifi.network_ssids(), **kwargs)
        self.wifi = wifi
        self.state = 'SELECT'

    def for_display(self) -> str:
        self.poll()
        msg = "WiFi Select Err"
        if 'SELECT' == self.state:
            msg = super().for_display()
        elif 'CONFIRM' == self.state:
            msg = 'Set Network[y/n]'
        elif 'SUCCESS' == self.state:
            msg = 'Connected'
        elif 'FAILED' == self.state:
            msg = 'Failed'
        elif 'ALREADY' == self.state:
            msg = "Connected already"
        elif 'CONNECTING' == self.state:
            msg = 'Connecting...'

        return msg

    def reset(self):
        super().reset()
        self.state = 'SELECT'

    def poll(self):
        if self.state != 'CONNECTING':
            return

        success = self.wifi.poll()
        if success is not None:
            self.state = 'SUCCESS' if success else 'FAILED'

    def select(self):
        ssid = self.current_value()
        self.wifi.select_network(self.wifi.id_lookup(ssid), blocking=False)

    def key_enter(self):
        if self.state == 'SELECT':
            if self.current_value() == self.wifi.connected_to():
                self.state = 'ALREADY'
            else:
                self.state = 'CONFIRM'
        elif self.state == 'CONFIRM':
            pass
        elif self.state == 'ALREADY':
            self.state = 'SELECT'
        else:
            self.state = 'DONE'
            self.set_done()

    def key_alpha_num(self, c):
        if self.state != 'CONFIRM':
            return

        c = c.lower()
        if c == 'y':
            self.select()
            self.state = 'CONNECTING'
        elif c == 'n':
            self.state = 'SELECT'


class WiFiMenu(Menu):
    def __init__(self):
        super().__init__("WiFi Settings")
        self.wifi = WiFiController('wlan0', sudo=True)
        self.wifi.load()

        ## Add submenues
        ssid = lambda: self.wifi.connected_to() or "No Network"
        addr = lambda: self.wifi.ip_address() or "No Address"
        conn = lambda: "Connected" if self.wifi.connected() else "Not Connected"

        self.add_submenu(MsgItem("Current Network", ssid))
        self.add_submenu(MsgItem("IP Address", addr))
        self.add_submenu(MsgItem("Status", conn))
        self.add_submenu(WiFiSelectItem(self.wifi, display_name="Select Network"))
        self.add_submenu(WiFiScanItem())

    def on_active(self):
        super().on_active()
        self.wifi.load(force=True)

    def reset(self):
        super().reset()
        self.wifi.load(force=True)

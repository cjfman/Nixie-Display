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

    def for_display(self):
        if self.state == 'SELECT':
            return super().for_display()
        if self.state == 'CONFIRM':
            return 'Set Network[y/n]'
        if self.state == 'SUCCESS':
            return 'Success'
        if self.state == 'FAILED':
            return 'Failed'
        if self.state == 'ALREADY':
            return "Connected already"

        return "WiFi Select Err"

    def reset(self):
        super().reset()
        self.state = 'SELECT'

    def select(self):
        ssid = self.current_value()
        success = self.wifi.select_network(self.wifi.id_lookup(ssid))
        return 'SUCCESS' if success else 'FAILED'

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
            self.set_done()

    def key_alpha_num(self, c):
        if self.state != 'CONFIRM':
            return
        if c.lower() == 'y':
            self.state = self.select()
        elif c.lower() == 'n':
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

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
    def __init__(self, device='wlan0', sudo=True, **kwargs):
        super().__init__("WiFi Networks", **kwargs)
        self.device = device
        self.sudo   = sudo
        self.proc   = None
        self.state  = None

    def reset(self):
        super().reset()
        self.proc  = None
        self.state = None

    def for_display(self) -> str:
        self.poll()
        msg = ""
        if self.state is None:
            msg = "Scan not started"
        elif 'RUNNING' == self.state:
            msg = "Scanning..."
        elif 'SELECT' == self.state:
            msg = super().for_display()
        elif 'FAILED' == self.state:
            msg = "Scan failed"
        else:
            msg = f"Error state: {self.state}"

        return msg

    def activate(self):
        self.run()
        self.state = 'RUNNING'

    def run(self):
        cmd = ['iwlist', self.device, 'scan']
        if self.sudo:
            cmd = ['sudo'] + cmd
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)

    def poll(self):
        if self.state != 'RUNNING':
            return

        ret = self.proc.poll()
        if ret is None:
            return
        if ret != 0:
            self.state = 'FAILED'

        networks = []
        for line in self.proc.stdout:
            match = re.search(r'ESSID:"(.+)"', line.decode('utf8'))
            if match:
                networks.append(match.groups()[0])

        header = f"Found {len(networks)} SSIDs"
        self.set_values([header] + networks)
        self.state = 'SELECT'


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

    def activate(self):
        super().activate()
        self.wifi.load(force=True)

    def reset(self):
        super().reset()
        self.wifi.load(force=True)

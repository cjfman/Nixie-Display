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

    def key_alpha_num(self, c):
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
        super().__init__("WiFi Select", wifi.network_ssids(), **kwargs)
        self.wifi = wifi
        self.state = 'select'

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

    def key_alpha_num(self, c):
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
        self.wifi.load()

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
        self.wifi.load(force=True)

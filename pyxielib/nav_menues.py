import re
import subprocess

from pyxielib.navigator import ListItem, Menu, MsgItem, SubcommandItem
from pyxielib.wifi_controller import WiFiController


class IpItem(SubcommandItem):
    def __init__(self):
        super().__init__("Show IP Address", ['ip', 'route', 'list', 'default'])

    def run(self):
        output = super().run()
        match = re.match(r"default via (\S+)", output)
        if match:
            return match.groups()[0]

        return "No IP Address"


class ListWiFiItem(ListItem):
    def __init__(self, device='wlan0'):
        super().__init__("WiFi Networks")
        self.started = False
        self.device = device

    def for_display(self):
        if not self.started:
            return "Scan not started"

        return super().for_display()

    def on_active(self):
        self.run()
        self.started = True

    def run(self):
        cmd = ['sudo', 'iwlist', self.device, 'scan']
        output = subprocess.run(cmd, capture_output=True, check=False).stdout.decode('utf8')
        networks = []
        for line in output.split("\n"):
            match = re.search(r'ESSID:"(.+)"', line)
            if match:
                networks.append(match.groups()[0])

        header = f"Found {len(networks)} SSIDs"
        self.set_values([header] + networks)


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
        self.add_submenu(ListWiFiItem())

    def on_active(self):
        super().on_active()
        self.wifi.load(force=True)

    def reset(self):
        super().reset()
        self.wifi.load(force=True)

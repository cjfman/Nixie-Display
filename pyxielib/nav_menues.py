import re

from pyxielib.navigator import Menu, MsgItem, SubcommandItem
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


class ListWiFiItem(SubcommandItem):
    def __init__(self, device='wlan0'):
        super().__init__("WiFi Networks", ['sudo', 'iwlist', device, 'scan'])
        self.networks = None
        self.idx = None

    def for_display(self):
        if self.networks is None:
            return "Scan not started"
        if self.idx is None:
            return f"Found {len(self.networks)} SSIDs"

        return self.networks[self.idx]

    def reset(self):
        super().reset()
        self.networks = None
        self.idx = None

    def run(self):
        output = super().run()
        self.networks = []
        self.idx = None
        for line in output.split("\n"):
            match = re.search(r'ESSID:"(.+)"', line)
            if match:
                self.networks.append(match.groups()[0])

        return f"Found {len(self.networks)} SSIDs"

    def key_up(self):
        if self.idx is None:
            self.idx = 0
        elif self.idx + 1 < len(self.networks):
            self.idx += 1

    def key_down(self):
        if self.idx <= 0:
            self.idx = None
        elif self.idx - 1 >= 0:
            self.idx -= 1

    def key_left(self):
        self.set_done()


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
        sef.wifi.load(force=True)

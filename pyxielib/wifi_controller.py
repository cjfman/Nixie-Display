import re
import subprocess

from dataclasses import dataclass, field
from typing import Set


@dataclass
class Network:
    id: int
    ssid: str
    bssid: str=""
    flags: Set = field(default_factory=set())

    @property
    def current(self):
        return ('CURRENT' in self.flags)

    @property
    def enabled(self):
        return ('DISABLED' not in self.flags)


class WiFiController:
    def __init__(self, device='wlan0', *, sudo=False):
        self.device = device
        self.networks = None
        self.current = None
        self.status = None
        self.sudo = sudo
        self.cmd = ['wpa_cli', '-i', self.device]
        if self.sudo:
            self.cmd = ['sudo'] + self.cmd

    def connected_to(self):
        self.load()
        if self.current is None:
            return None

        return self.current.ssid

    def connected(self):
        self.load()
        if self.status is None or 'wpa_state' not in self.status:
            return None

        return (self.status['wpa_state'] == 'COMPLETED')

    def ip_address(self):
        self.load()
        if self.status is None or 'ip_address' not in self.status:
            return None

        return self.status['ip_address']

    def loaded(self):
        return (self.networks is not None)

    def load(self, force=False) -> bool:
        """Load the current wifi configuration"""
        if self.networks is not None and not force:
            return True

        ## Get the networks
        cmd = self.cmd + ['list_networks']
        proc = subprocess.run(cmd, capture_output=True, check=False)
        if proc.returncode != 0:
            return False

        ## Process the output
        output = proc.stdout.decode('utf8').split("\n")
        self.networks = {}
        for line in output[1:]:
            if not line:
                continue
            ## network id / ssid / bssid / flags
            nid, ssid, bssid, flags = line.split("\t")
            flags = set(re.findall(r"([\w+|-]+)", flags))
            nid = int(nid)
            network = Network(nid, ssid, bssid, flags)
            self.networks[nid] = network
            if network.current:
                self.current = network

        self.status = self.get_status()
        return True

    def add_network(self, ssid, password=None, *, priority=None, save=False, connect=False) -> bool:
        """Add a wifi network"""
        ## pylint: disable=too-many-return-statements
        cmd = self.cmd + ['add_network']
        proc = subprocess.run(cmd, capture_output=True, check=False)
        if proc.returncode != 0:
            return False

        nid = int(proc.stdout.decode('utf8').strip())

        ## Set the SSID
        if not self.set_network(nid, 'ssid', ssid):
            ## Remove network on failure
            self.remove_network(nid)
            return False

        ## Set password
        if password is not None:
            if not self.set_network(nid, 'psk', password):
                ## Remove network on failure
                self.remove_network(nid)
                return False

        ## Set priority
        if priority is not None:
            if not self.set_network(nid, 'priority', priority):
                ## Remove network on failure
                self.remove_network(nid)
                return False

        ## Save
        if save and not self.save():
            return False

        ## Enable
        if not self.enable_network(nid):
            return False

        ## Connect
        if connect and not self.select_network(nid):
            self.select_network(self.current.id)

        return self.load(force=True)

    def select_network(self, nid) -> bool:
        """Select a network"""
        return self._run_cmd(['select_network', str(nid)])

    def enable_network(self, nid) -> bool:
        """Enable a network"""
        return self._run_cmd(['enable_network', str(nid)])

    def remove_network(self, nid) -> bool:
        """Remove a network"""
        return self._run_cmd(['remove_network', str(nid)])

    def set_network(self, nid, key, value) -> True:
        """Set a network property"""
        if isinstance(value, str):
            value = f'"{value}"'

        return self._run_cmd(['set_network', str(nid), key, str(value)])

    def save(self):
        """Save the network config"""
        return self._run_cmd('save_config')

    def get_status(self):
        proc = subprocess.run(self.cmd + ['status'], capture_output=True, check=False)
        if proc.returncode != 0:
            return None

        status = {}
        for line in proc.stdout.decode('utf8').split('\n'):
            if '=' not in line:
                continue

            args = line.split('=')
            status[args[0]] = args[1]

        return status

    def _run_cmd(self, cmd):
        """Run a simple command"""
        if isinstance(cmd, str):
            cmd = [cmd]

        proc = subprocess.run(self.cmd + cmd, capture_output=True, check=False)
        return (proc.returncode == 0)

import logging
import os
import re
import subprocess

from typing import Optional

logger = logging.getLogger(__name__)

_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HELPER = os.path.join(_REPO_DIR, 'scripts', 'wifi_ap_root.sh')
_CONF_FILE = os.path.expanduser('~/.nixie/wifi_ap.conf')

DEFAULT_AP_SSID = 'nixie-control'
DEFAULT_AP_PASSWORD = 'neon-tube-backdoor-64'


class WiFiAPConfig:
    """SSID/password for the on-demand access point (from the wifi_ap: config)."""
    def __init__(self, ssid=DEFAULT_AP_SSID, password=DEFAULT_AP_PASSWORD):
        if not self._valid_ssid(ssid):
            logger.warning("Invalid wifi_ap ssid; using default")
            ssid = DEFAULT_AP_SSID
        if not self._valid_psk(password):
            logger.warning("Invalid wifi_ap password; using default")
            password = DEFAULT_AP_PASSWORD
        self.ssid = ssid
        self.password = password

    @staticmethod
    def _valid_ssid(value) -> bool:
        return isinstance(value, str) and 1 <= len(value) <= 32

    @staticmethod
    def _valid_psk(value) -> bool:
        return isinstance(value, str) and 8 <= len(value) <= 63

    @classmethod
    def from_dict(cls, data) -> 'WiFiAPConfig':
        data = data or {}
        return cls(
            ssid=data.get('ssid', DEFAULT_AP_SSID),
            password=data.get('password', DEFAULT_AP_PASSWORD),
        )


class WiFiAPController:
    """Toggle the on-demand WiFi access point via the root helper script.

    Mirrors WiFiController's `sudo -n` convention: on the Pi the helper is
    NOPASSWD-whitelisted, so calls are silent; elsewhere they fail fast.
    """
    def __init__(self, iface='wlan0'):
        self.iface = iface

    def _run(self, *args) -> Optional[subprocess.CompletedProcess]:
        cmd = ['sudo', '-n', _HELPER] + list(args)
        try:
            return subprocess.run(cmd, capture_output=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError) as e:
            logger.warning(f"WiFi AP helper failed: {e}")
            return None

    def status(self) -> bool:
        """True when the access point is currently broadcasting."""
        proc = self._run('status')
        if proc is None or proc.returncode != 0:
            return False

        return proc.stdout.decode(errors='replace').strip().startswith('on')

    def ip_address(self) -> Optional[str]:
        """The AP's wlan0 address while it's up, else None."""
        proc = self._run('status')
        if proc is None or proc.returncode != 0:
            return None

        match = re.search(r'\b(\d{1,3}(?:\.\d{1,3}){3})\b', proc.stdout.decode(errors='replace'))
        if match:
            return match.group(1)

        return None

    def enable(self, ssid, password) -> bool:
        if not self._write_conf(ssid, password):
            return False
        proc = self._run('up', _CONF_FILE)
        return proc is not None and proc.returncode == 0

    def disable(self) -> bool:
        proc = self._run('down')
        return proc is not None and proc.returncode == 0

    @staticmethod
    def _write_conf(ssid, password) -> bool:
        """Write the SSID/password to a 0600 file for the root helper to read."""
        try:
            os.makedirs(os.path.dirname(_CONF_FILE), exist_ok=True)
            fd = os.open(_CONF_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as f:
                f.write(f"ssid={ssid}\npassword={password}\n")
            return True
        except OSError as e:
            logger.error(f"Could not write {_CONF_FILE}: {e}")
            return False

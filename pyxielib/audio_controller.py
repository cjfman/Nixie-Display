import array
import io
import logging
import math
import os
import re
import subprocess
import threading
import time
import wave
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

_SOUNDS_BASE = '/usr/share/sounds/freedesktop/stereo'


def _generate_beep_wav(freq=1000, duration=1.0, sample_rate=44100) -> bytes:
    """Generate a sine-wave beep as raw WAV bytes (mono 16-bit PCM)."""
    n = int(sample_rate * duration)
    samples = array.array('h', (
        int(32767 * math.sin(2 * math.pi * freq * i / sample_rate))
        for i in range(n)
    ))
    buf = io.BytesIO()
    with wave.open(buf, 'w') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(samples)
    return buf.getvalue()


@dataclass
class AudioSink:
    name: str
    index: int
    description: str
    is_bluetooth: bool
    bt_mac: Optional[str] = None


def _looks_like_mac(text) -> bool:
    """True if text is just a MAC address. BlueZ uses the MAC as the device
    name (with ':' or '-' separators) when a device has no friendly name."""
    return re.fullmatch(r'[0-9A-Fa-f]{2}([:_-][0-9A-Fa-f]{2}){5}', text.strip()) is not None


@dataclass
class BluetoothDevice:
    mac: str
    name: str
    connected: bool = False
    paired: bool = False
    trusted: bool = False

    @property
    def named(self) -> bool:
        """True if the device advertised a real name (not just its MAC)."""
        return bool(self.name) and not _looks_like_mac(self.name)


_MUTE_CACHE_TTL = 0.5  ## seconds


class AudioController:
    def __init__(self):
        self._scan_proc = None
        self._scan_start = 0.0
        self._scan_timeout = 10
        self._pair_thread: Optional[threading.Thread] = None
        self._pair_result: Optional[bool] = None
        self._test_proc = None
        self._test_thread: Optional[threading.Thread] = None
        self._test_result: Optional[bool] = None
        self._mute_cache: Optional[bool] = None
        self._mute_cache_time: float = 0.0

    # --- PulseAudio / PipeWire ---

    def list_sinks(self) -> List[AudioSink]:
        try:
            result = subprocess.run(
                ['pactl', 'list', 'sinks'],
                capture_output=True, check=False, timeout=5,
            )
        except Exception:
            return []
        if result.returncode != 0:
            return []
        return self._parse_sinks(result.stdout.decode('utf-8', errors='replace'))

    @staticmethod
    def _parse_sinks(output) -> List[AudioSink]:
        sinks = []
        idx = None
        current_name = None
        current_desc = None
        for line in output.splitlines():
            m = re.match(r'^Sink #(\d+)', line)
            if m:
                if current_name is not None:
                    sinks.append(AudioController._make_sink(idx, current_name, current_desc or current_name))
                idx = int(m.group(1))
                current_name = None
                current_desc = None
                continue
            stripped = line.strip()
            if stripped.startswith('Name:'):
                current_name = stripped.split(':', 1)[1].strip()
            elif stripped.startswith('Description:'):
                current_desc = stripped.split(':', 1)[1].strip()
        if current_name is not None:
            sinks.append(AudioController._make_sink(idx, current_name, current_desc or current_name))
        return sinks

    @staticmethod
    def _make_sink(index, name, description) -> AudioSink:
        is_bt = name.startswith('bluez_output.')
        bt_mac = None
        if is_bt:
            m = re.search(r'bluez_output\.([0-9A-Fa-f_]{17})', name)
            if m:
                bt_mac = m.group(1).replace('_', ':')
        return AudioSink(name=name, index=index, description=description,
                         is_bluetooth=is_bt, bt_mac=bt_mac)

    def get_default_sink(self) -> Optional[str]:
        try:
            result = subprocess.run(
                ['pactl', 'get-default-sink'],
                capture_output=True, check=False, timeout=5,
            )
        except Exception:
            return None
        if result.returncode != 0:
            return None
        return result.stdout.decode('utf-8', errors='replace').strip() or None

    def get_default_sink_description(self) -> Optional[str]:
        default = self.get_default_sink()
        if default is None:
            return None
        for sink in self.list_sinks():
            if sink.name == default:
                return sink.description
        return default

    def set_default_sink(self, name) -> bool:
        try:
            result = subprocess.run(
                ['pactl', 'set-default-sink', name],
                capture_output=True, check=False, timeout=5,
            )
        except Exception:
            return False
        return result.returncode == 0

    def is_muted(self) -> bool:
        now = time.time()
        if self._mute_cache is not None and now - self._mute_cache_time < _MUTE_CACHE_TTL:
            return self._mute_cache
        try:
            result = subprocess.run(
                ['pactl', 'get-sink-mute', '@DEFAULT_SINK@'],
                capture_output=True, check=False, timeout=5,
            )
        except Exception:
            return False
        muted = 'yes' in result.stdout.decode('utf-8', errors='replace').lower()
        self._mute_cache = muted
        self._mute_cache_time = now
        return muted

    def set_mute(self, muted) -> bool:
        try:
            result = subprocess.run(
                ['pactl', 'set-sink-mute', '@DEFAULT_SINK@', '1' if muted else '0'],
                capture_output=True, check=False, timeout=5,
            )
        except Exception:
            return False
        if result.returncode == 0:
            self._mute_cache = muted
            self._mute_cache_time = time.time()
        return result.returncode == 0

    # --- Test sound ---

    def play_test_sound_async(self, sound_name='audio-test-signal') -> None:
        self.stop_test()
        self._test_result = None
        self._test_thread = threading.Thread(
            target=self._test_worker, args=(sound_name,), daemon=True,
        )
        self._test_thread.start()

    def _test_worker(self, sound_name) -> None:
        path = os.path.join(_SOUNDS_BASE, sound_name + '.oga')
        try:
            if os.path.isfile(path):
                self._test_proc = subprocess.Popen(
                    ['paplay', path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            else:
                logger.info("Sound file not found: %s; generating 1 kHz beep", path)
                self._test_proc = subprocess.Popen(
                    ['aplay', '-q', '-'],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                self._test_proc.stdin.write(_generate_beep_wav())
                self._test_proc.stdin.close()
            self._test_proc.wait()
            self._test_result = True
        except Exception:
            self._test_result = False
        finally:
            self._test_proc = None

    def poll_test(self) -> Optional[bool]:
        if self._test_thread is None:
            return self._test_result
        if self._test_thread.is_alive():
            return None
        self._test_thread = None
        return self._test_result

    def stop_test(self) -> None:
        if self._test_proc is not None:
            try:
                self._test_proc.terminate()
            except Exception:
                pass
        self._test_proc = None
        self._test_thread = None
        self._test_result = None

    # --- Bluetooth scanning ---

    def scan_start(self, timeout=10) -> None:
        self.scan_cancel()
        try:
            self._scan_proc = subprocess.Popen(
                ['bluetoothctl', 'scan', 'on'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            self._scan_proc = None
        self._scan_start = time.time()
        self._scan_timeout = timeout

    def scan_poll(self) -> Optional[List[BluetoothDevice]]:
        if self._scan_proc is None:
            return self.scan_devices()
        if time.time() - self._scan_start < self._scan_timeout:
            return None
        self.scan_cancel()
        return self.scan_devices()

    def scan_cancel(self) -> None:
        if self._scan_proc is None:
            return
        try:
            self._scan_proc.terminate()
            self._scan_proc.wait(timeout=2)
        except Exception:
            pass
        self._scan_proc = None

    def scan_devices(self) -> List[BluetoothDevice]:
        return self._run_bt_devices([])

    def list_paired_devices(self) -> List[BluetoothDevice]:
        return self._run_bt_devices(['Paired'])

    def _run_bt_devices(self, extra_args) -> List[BluetoothDevice]:
        try:
            result = subprocess.run(
                ['bluetoothctl', 'devices'] + extra_args,
                capture_output=True, check=False, timeout=5,
            )
        except Exception:
            return []
        if result.returncode != 0:
            return []
        return self._parse_bt_devices(result.stdout.decode('utf-8', errors='replace'))

    @staticmethod
    def _parse_bt_devices(output) -> List[BluetoothDevice]:
        devices = []
        for line in output.splitlines():
            m = re.match(r'Device\s+([0-9A-Fa-f:]{17})\s+(.*)', line)
            if m:
                devices.append(BluetoothDevice(mac=m.group(1), name=m.group(2).strip()))
        return devices

    def remove_device(self, mac) -> bool:
        try:
            result = subprocess.run(
                ['bluetoothctl', 'remove', mac],
                capture_output=True, check=False, timeout=10,
            )
        except Exception:
            return False
        return result.returncode == 0

    # --- Bluetooth pairing ---

    def pair_and_connect_async(self, mac) -> None:
        self._pair_result = None
        self._pair_thread = threading.Thread(
            target=self._pair_worker, args=(mac,), daemon=True,
        )
        self._pair_thread.start()

    ## (command, seconds to wait afterwards). Pairing and connection complete
    ## asynchronously, so each step is given time before the next is issued.
    _PAIR_STEPS = (
        ('power on', 0.5),
        ('agent on', 0.5),
        ('default-agent', 0.5),
        ('scan on', 3.0),       ## keep the adapter discovering so the device is connectable
        ('pair {mac}', 8.0),    ## pairing + PIN/auth exchange can take several seconds
        ('trust {mac}', 1.0),
        ('connect {mac}', 6.0),
        ('scan off', 0.5),
    )

    def _pair_worker(self, mac) -> None:
        try:
            output = self._pair_session(mac)
            logger.info("bluetoothctl pair session for %s:\n%s", mac, output.strip())
            self._pair_result = self._is_connected(mac)
            if not self._pair_result:
                logger.warning("Pairing/connecting to %s did not succeed", mac)
        except Exception as e:
            logger.warning("Bluetooth pairing error for %s: %s", mac, e)
            self._pair_result = False

    def _pair_session(self, mac) -> str:
        """Drive one interactive bluetoothctl session through the full
        power/agent/pair/trust/connect sequence; return its combined output.

        A single session is required because the pairing agent is registered
        per bluetoothctl process — 'agent on'/'default-agent' must run in the
        same session as 'pair', which the previous one-shot-per-command
        approach could not do, so pairing always failed for lack of an agent.
        A reader thread drains stdout while commands are written so a chatty
        session can't deadlock on a full pipe."""
        proc = subprocess.Popen(
            ['bluetoothctl'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True,
        )
        chunks: List[str] = []
        reader = threading.Thread(target=lambda: chunks.append(proc.stdout.read()), daemon=True)
        reader.start()
        try:
            for cmd, wait in self._PAIR_STEPS:
                proc.stdin.write(cmd.format(mac=mac) + '\n')
                proc.stdin.flush()
                time.sleep(wait)
            proc.stdin.write('quit\n')
            proc.stdin.flush()
        except Exception:
            pass
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        reader.join(timeout=2)
        return ''.join(c for c in chunks if c)

    def _is_connected(self, mac) -> bool:
        """Source of truth for pairing success: query the device and check it
        reports paired/connected, rather than trusting bluetoothctl exit codes
        (which are unreliable)."""
        try:
            result = subprocess.run(
                ['bluetoothctl', 'info', mac],
                capture_output=True, check=False, timeout=10,
            )
        except Exception:
            return False
        out = result.stdout.decode('utf-8', errors='replace')
        paired = re.search(r'Paired:\s*yes', out, re.IGNORECASE) is not None
        connected = re.search(r'Connected:\s*yes', out, re.IGNORECASE) is not None
        return paired or connected

    def poll_pair(self) -> Optional[bool]:
        if self._pair_thread is None:
            return None
        if self._pair_result is None:
            return None
        self._pair_thread = None
        return self._pair_result

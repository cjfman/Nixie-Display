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


@dataclass
class BluetoothDevice:
    mac: str
    name: str
    connected: bool = False
    paired: bool = False
    trusted: bool = False


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
        try:
            result = subprocess.run(
                ['pactl', 'get-sink-mute', '@DEFAULT_SINK@'],
                capture_output=True, check=False, timeout=5,
            )
        except Exception:
            return False
        return 'yes' in result.stdout.decode('utf-8', errors='replace').lower()

    def set_mute(self, muted) -> bool:
        try:
            result = subprocess.run(
                ['pactl', 'set-sink-mute', '@DEFAULT_SINK@', '1' if muted else '0'],
                capture_output=True, check=False, timeout=5,
            )
        except Exception:
            return False
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

    def _pair_worker(self, mac) -> None:
        try:
            r = subprocess.run(
                ['bluetoothctl', 'pair', mac],
                capture_output=True, check=False, timeout=30,
            )
            if r.returncode != 0:
                self._pair_result = False
                return
            subprocess.run(
                ['bluetoothctl', 'trust', mac],
                capture_output=True, check=False, timeout=10,
            )
            r = subprocess.run(
                ['bluetoothctl', 'connect', mac],
                capture_output=True, check=False, timeout=30,
            )
            self._pair_result = (r.returncode == 0)
        except Exception:
            self._pair_result = False

    def poll_pair(self) -> Optional[bool]:
        if self._pair_thread is None:
            return None
        if self._pair_result is None:
            return None
        self._pair_thread = None
        return self._pair_result

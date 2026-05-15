import logging
import os
import sys
import threading
import time

from queue import Queue

from .pyxieutil import PyxieError

logger = logging.getLogger(__name__)

ENABLED = True
try:
    import evdev as ev
    from evdev.events import KeyEvent
    #from evdev import categorize, event_factory, ecodes
except Exception as e:
    logger.warning(f"Failed to initialize the key_watcher package. Disabling it {e}")
    ENABLED = False

TERMINAL_ENABLED = True
try:
    import termios
    import tty
    import select as _select
except ImportError:
    TERMINAL_ENABLED = False

SHIFTS = {
    "`": "~",
    "1": "!",
    "2": "@",
    "3": "#",
    "4": "$",
    "5": "%",
    "6": "^",
    "7": "&",
    "8": "*",
    "9": "(",
    "0": ")",
    "-": "_",
    "=": "+",
    "[": "{",
    "]": "}",
    ";": ":",
    "'": '"',
    ",": "<",
    ".": ">",
    "/": "?",
    "\\": "|",
}

SPECIAL_KEYS = {
    'GRAVE':      '`',
    'MINUS':      '-',
    'EQUAL':      '=',
    'LEFTBRACE':  '[',
    'RIGHTBRACE': ']',
    'BACKSLASH':  '\\',
    'SEMICOLON':  ';',
    'APOSTROPHE': "'",
    'COMMA':      ',',
    'DOT':        '.',
    'SLASH':      '/',
    'SPACE':      ' ',
}

class KeyWatcher:
    def __init__(self, event_path, *, owner=None, trigger=None, release=None, hold=True):
        if not ENABLED:
            raise PyxieError(f"Cannot instantiate a {self.__class__.__name__}. The key_watcher package is disabled")

        self.event_path = event_path
        self.owner = owner
        self.trigger = set(trigger or {})
        self.release = set(release or {})
        self.hold = hold
        self.dev = None
        self.running = True
        self.stopped = False
        self.keys_down = set()
        self.active = (not self.trigger)
        self.thread = threading.Thread(target=self.run)
        self.queue = Queue()

        ## Thread options
        self.thread.daemon = True
        self.thread.start()

    def reset(self):
        """Reset the key watcher"""
        self.active = (not self.trigger)
        self.queue = Queue()

    def shifted(self) -> bool:
        """Returns True if any of the shift keys are being held"""
        return any(x in self.keys_down for x in ('KEY_LEFTSHIFT', 'KEY_RIGHTSHIFT'))

    def empty_queue(self):
        while not self.queue.empty():
            self.queue.get()

    @staticmethod
    def make_shifted(key) -> str:
        """Make a key shifted assuing US keyboard"""
        if key.isalpha():
            return key.upper()
        if key in SHIFTS:
            return SHIFTS[key]

        return key

    def code_to_char(self, key) -> str:
        """Turn a KeyEvent into a char"""
        key = key.replace('KEY_', '')
        key = SPECIAL_KEYS.get(key, key)
        if len(key) == 1:
            if self.shifted():
                key = self.make_shifted(key)
            else:
                key = key.lower()

        return key

    @staticmethod
    def _parse_input_device_block(block):
        """Parse one stanza from /proc/bus/input/devices.

        Returns (has_sysrq, '/dev/input/eventN') or (False, None).
        """
        has_sysrq = False
        event_path = None
        for line in block.splitlines():
            if line.startswith('H: Handlers='):
                handlers = line.split('=', 1)[1].split()
                has_sysrq = 'sysrq' in handlers
                for handler in handlers:
                    if handler.startswith('event'):
                        event_path = f'/dev/input/{handler}'
        return has_sysrq, event_path

    def _find_keyboard(self, prefer=None):
        """Find a keyboard event device by reading /proc/bus/input/devices.

        Uses the presence of the 'sysrq' handler as the discriminator: the
        kernel only registers it for real keyboards, not for HDMI, power
        buttons, or other virtual devices. If prefer is given and is a
        keyboard, it is returned first.
        """
        try:
            with open('/proc/bus/input/devices') as f:
                first = None
                for block in f.read().strip().split('\n\n'):
                    has_sysrq, path = self._parse_input_device_block(block)
                    if has_sysrq and path is not None:
                        if first is None:
                            first = path
                        if path == prefer:
                            return path
                return first
        except OSError:
            pass
        return None

    def run(self):
        """Main watcher loop"""
        logger.info("KeyWatcher thread starting")
        while self.running:
            try:
                if self.dev is None:
                    path = self._find_keyboard(prefer=self.event_path)
                    if path is None:
                        logger.warning("No keyboard found, retrying")
                        time.sleep(1)
                        continue
                    if path != self.event_path:
                        logger.info(f"Keyboard found at '{path}' instead of '{self.event_path}'")
                    logger.info(f"Opening '{path}'")
                    self.dev = ev.InputDevice(path)
                    logger.info(f"Opened '{path}'")

                self.read_loop()
            except OSError as e:
                logger.warning(f"Device error on '{self.event_path}': {e}, retrying")
                if self.dev is not None:
                    try:
                        self.dev.close()
                    except OSError:
                        pass
                self.dev = None
                time.sleep(1)

        self.stopped = True
        logger.info("KeyWatcher thread stopped")

    def read_loop(self):
        """Read key events"""
        ## pylint: disable=too-many-branches
        for event in self.dev.read_loop():
            if not self.running:
                break
            if event.type != ev.ecodes.EV_KEY:
                continue

            k_event = ev.categorize(event)
            ## Update set of down keys
            if k_event.keystate == KeyEvent.key_down:
                logger.debug("%s", k_event)
                self.keys_down.add(k_event.keycode)
                ## Check for trigger or release key combo
                if self.trigger and self.trigger == self.keys_down:
                    self.active = True
                    logger.info("KeyWatcher triggered")
                    if self.owner is not None:
                        self.owner.wake()
                elif self.release and self.release == self.keys_down and self.active:
                    self.active = False
                    self.empty_queue()
                    self.queue.put('USER_INTERRUPT')
                    logger.info("KeyWatcher interrupted")
                    if self.owner is not None:
                        self.owner.wake()
                elif self.active:
                    ## Only add event to queue if it's not a trigger or release
                    self.queue.put(k_event)
            elif k_event.keystate == KeyEvent.key_up and k_event.keycode in self.keys_down:
                self.keys_down.remove(k_event.keycode)
            elif k_event.keystate == KeyEvent.key_hold:
                self.keys_down.add(k_event.keycode) ## Add just in case it was somehow missed before
                if self.hold and self.active:
                    self.queue.put(k_event)

    def can_pop(self):
        return (not self.queue.empty())

    def pop(self):
        ## Return now if there's nothing
        if self.queue.empty():
            return None

        ## Check for a key
        key = self.queue.get()
        if key == 'USER_INTERRUPT':
            raise KeyboardInterrupt()
        if not key.keycode.startswith('KEY_'):
            return None

        ## Clean up the key
        return self.code_to_char(key.keycode)

    def stop(self):
        self.running = False
        try:
            ## Inject key to trigger wake
            ## Other options for keys KEY_LOGOFF, KEY_EXIT, KEY_BREAK, KEY_RIGHTMETA
            ui = ev.UInput()
            ui.write(ev.ecodes.EV_KEY, ev.ecodes.KEY_ESC, 1)  # KEY down
            ui.write(ev.ecodes.EV_KEY, ev.ecodes.KEY_ESC, 0)  # KEY up
            ui.syn()
        except:
            pass

        return self.thread.join(timeout=5)


class TerminalKeyWatcher:
    """Reads keyboard input from stdin for terminal-mode debugging.

    Press ESC to open the menu. Arrow keys, enter, backspace, and printable
    characters are forwarded while the menu is active. ESC at the root menu
    closes the menu. Ctrl+C exits the process via SIGINT as normal.
    """

    def __init__(self, *, owner=None):
        if not TERMINAL_ENABLED:
            raise PyxieError("TerminalKeyWatcher requires termios/tty (Unix only)")
        if not sys.stdin.isatty():
            raise PyxieError("TerminalKeyWatcher requires stdin to be a TTY")

        self.owner   = owner
        self.active  = False
        self.queue   = Queue()
        self.running = True
        self.stopped = False
        self._fd     = sys.stdin.fileno()
        self.thread  = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def reset(self):
        """Deactivate and discard pending keystrokes"""
        self.active = False
        self.queue = Queue()

    def can_pop(self) -> bool:
        return not self.queue.empty()

    def pop(self):
        if self.queue.empty():
            return None
        return self.queue.get()

    def stop(self):
        self.running = False
        self.thread.join(timeout=2)

    def _run(self):
        old_settings = termios.tcgetattr(self._fd)
        try:
            tty.setcbreak(self._fd)
            while self.running:
                r, _, _ = _select.select([self._fd], [], [], 0.1)
                if r:
                    self._handle_byte(os.read(self._fd, 1))
        finally:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, old_settings)
        self.stopped = True

    def _peek(self, timeout=0.05):
        """Return the next stdin byte if one arrives within timeout, else None."""
        r, _, _ = _select.select([self._fd], [], [], timeout)
        return os.read(self._fd, 1) if r else None

    def _handle_byte(self, b: bytes):
        if b == b'\x1b':
            b2 = self._peek()
            if b2 is None:
                ## Bare ESC: toggle menu
                if not self.active:
                    self.active = True
                    if self.owner is not None:
                        self.owner.wake()
                else:
                    self.queue.put('ESC')
                return

            if b2 != b'[':
                return  ## Unrecognised sequence; ignore

            direction = self._peek()
            if direction is not None and self.active:
                arrow_map = {b'A': 'UP', b'B': 'DOWN', b'C': 'RIGHT', b'D': 'LEFT'}
                key = arrow_map.get(direction)
                if key:
                    self.queue.put(key)
            return

        if not self.active:
            return

        if b in (b'\n', b'\r'):
            self.queue.put('ENTER')
        elif b in (b'\x7f', b'\x08'):
            self.queue.put('BACKSPACE')
        else:
            try:
                c = b.decode('utf-8')
                if c.isprintable():
                    self.queue.put(c)
            except UnicodeDecodeError:
                pass

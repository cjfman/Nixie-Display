import threading
import time

from queue import Queue

from .pyxieutil import PyxieError

ENABLED = True
try:
    import evdev as ev
    from evdev.events import KeyEvent
    #from evdev import categorize, event_factory, ecodes
except Exception as e:
    print(f"Failed to initialize the key_watcher package. Disabling it {e}")
    ENABLED = False

DEBUG = False
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

    def run(self):
        """Main watcher loop"""
        print("KeyWatcher thread starting")
        while self.running:
            try:
                if self.dev is None:
                    print(f"Opening '{self.event_path}'")
                    self.dev = ev.InputDevice(self.event_path)
                    print(f"Opened '{self.event_path}'")

                self.read_loop()
            except FileNotFoundError:
                ## File wasn't found try again after a nap
                print(f"Failed to open '{self.event_path}'")
                self.dev = None
                time.sleep(1)

        self.stopped = True
        print("KeyWatcher thread stopped")

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
                if DEBUG:
                    print(k_event)
                self.keys_down.add(k_event.keycode)
                ## Check for trigger or release key combo
                if self.trigger and self.trigger == self.keys_down:
                    self.active = True
                    print("KeyWatcher triggered")
                    if self.owner is not None:
                        self.owner.wake()
                elif self.release and self.release == self.keys_down and self.active:
                    self.active = False
                    self.empty_queue()
                    self.queue.put('USER_INTERRUPT')
                    print("KeyWatcher interrupted")
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

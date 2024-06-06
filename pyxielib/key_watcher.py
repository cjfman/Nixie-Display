import threading

from queue import Queue

import evdev as ev
from evdev.events import KeyEvent
#from evdev import categorize, event_factory, ecodes

class KeyWatcher:
    def __init__(self, event_path, *, trigger=None, release=None, hold=True):
        self.event_path = event_path
        self.thread = threading.Thread(target=self.run)
        self.dev = ev.InputDevice(event_path)
        self.running = True
        self.stopped = False
        self.thread.daemon = True
        self.hold = hold
        self.keys_down = set()
        self.trigger = set(trigger or {})
        self.release = set(release or {})
        self.active = (not self.trigger)
        self.queue = Queue()

        self.thread.start()

    def reset(self):
        self.queue = Queue()

    def run(self):
        for event in self.dev.read_loop():
            if not self.running:
                break
            if event.type != ev.ecodes.EV_KEY:
                continue

            k_event = ev.categorize(event)
            ## Update set of down keys
            if k_event.status == KeyEvent.key_down:
                self.keys_down.add(k_event.key_code)
                ## Check for trigger or release key combo
                if self.trigger and self.trigger == self.keys_down:
                    self.active = True
                elif self.release and self.release == self.keys_down:
                    self.active = False
                elif self.active:
                    ## Only add event to queue if it's not a trigger or release
                    self.queue.put(k_event)
            elif k_event.status == KeyEvent.key_up:
                self.keys_down.remove(k_event.key_code)
            elif k_event.status == KeyEvent.key_hold:
                self.keys_down.add(k_event.key_code) ## Add just in case it was somehow missed before
                if self.hold and self.active:
                    self.queue.put(k_event)

        self.stopped = True

    def can_pop(self):
        return (not self.queue.empty())

    def pop(self):
        if not self.queue.empty():
            return None

        return self.queue.get().replace("KEY_", "")

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

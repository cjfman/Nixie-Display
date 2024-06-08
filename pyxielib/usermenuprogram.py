import pyxielib.animation_library as animationlib
from pyxielib import nav_menues
from pyxielib import navigator as navlib
from pyxielib.key_watcher import KeyWatcher
from pyxielib.navigator import Menu, Navigator
from pyxielib.program import Program


class UserMenuProgram(Program):
    def __init__(self, event_path):
        super().__init__("User Control")
        self.active      = False
        self.should_exit = False
        self.old_msg     = None
        self.watcher = KeyWatcher(event_path, hold=False,
            trigger={
                'KEY_LEFTCTRL',
                'KEY_LEFTALT',
                'KEY_F4',
            },
            release={
                'KEY_LEFTCTRL',
                'KEY_C',
            }
        )
        self.navigator = Navigator(Menu("Nixie Menu", [
            nav_menues.IpItem(),
            nav_menues.WiFiMenu(),
            navlib.MirrorItem("Mirror Mode"),
            nav_menues.RebootItem(),
            nav_menues.ShutdownItem(),
            nav_menues.ExitItem("Exit Program"),
        ]))

    def reset(self):
        super().reset()
        self.navigator.reset()
        self.watcher.reset()
        self.active      = False
        self.should_exit = False
        self.old_msg     = None

    def interrupt(self):
        return (self.active or self.watcher.active or self.watcher.can_pop())

    def done(self):
        return self.should_exit

    def makeAnimation(self):
        ## Handle all queued keys
        msg = None
        if self.watcher.can_pop() and not self.should_exit:
            self.active = True
            key = None
            try:
                key = self.watcher.pop()
                if key is not None:
                    msg = self.navigator.key_entry(key)
            except KeyboardInterrupt as e:
                print("User requested exit from program")
                raise e
            
        if msg is None:
            if not self.navigator.should_exit:
                msg = self.navigator.for_display()
            else:
                print("User requested exit from menu")
                self.should_exit = True
                self.active = False
                self.watcher.reset()
                self.navigator.reset()
                return None

        if msg == self.old_msg:
            return None

        self.old_msg = msg
        return animationlib.MarqueeAnimation.fromText(msg, 16, freeze=True)

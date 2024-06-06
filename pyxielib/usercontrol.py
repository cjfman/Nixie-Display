import pyxielib.animation_library as animationlib
from pyxielib import nav_menues
from pyxielib.key_watcher import KeyWatcher
from pyxielib.navigator import Menu, Navigator
from pyxielib.program import Program


class UserControl(Program):
    def __init__(self, event_path):
        super().__init__("User Control")
        self.watcher = KeyWatcher(event_path, hold=False)
        self.navigator = Navigator(Menu("Nixie Menu", [
            nav_menues.IpItem(),
            nav_menues.WiFiMenu(),
            nav_menues.RebootItem(),
            nav_menues.ShutdownItem(),
        ]))
        self.msg         = None
        self.should_exit = False

    def reset(self):
        super().reset()
        self.navigator.reset()
        self.watcher.reset()
        self.should_exit = False

    def done(self):
        return self.should_exit

    def getAnimation(self):
        if not self.watcher.active:
            self.should_exit = True
            return None

        ## Handle all queued keys
        msg = None
        while self.watcher.can_pop():
            key = self.watcher.pop()
            msg = self.navigator.key_entry(key)

        ## No change
        if msg == self.msg:
            return None

        self.msg = msg
        return animationlib.makeTextSequence([msg], 1)

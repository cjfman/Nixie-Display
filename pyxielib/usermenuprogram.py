import pyxielib.animation_library as animationlib
from pyxielib import nav_menues
from pyxielib import navigator as navlib
from pyxielib.animation import Animation
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
        """Reset the key watcher and user menu"""
        super().reset()
        self.navigator.reset()
        self.watcher.reset()
        self.active      = False
        self.should_exit = False
        self.old_msg     = None

    def interrupt(self) -> bool:
        """Returns true if active animations and programs should be interrupted to check the user menu"""
        return (self.active or self.watcher.active or self.watcher.can_pop())

    def done(self) -> bool:
        return self.should_exit

    def makeAnimation(self) -> Animation:
        """Make the menu animation"""
        ## Check the key watcher
        msg = None
        if self.watcher.can_pop() and not self.should_exit:
            self.active = True
            key = None
            try:
                key = self.watcher.pop()
                if key is not None:
                    msg = self.navigator.key_entry(key)
            except KeyboardInterrupt:
                self.menu_exit()
                return None

        ## If the key watcher didn't return a key, check the
        ## menu for an update anyway
        if msg is None:
            if self.navigator.should_exit:
                self.menu_exit()
                return None

            msg = self.navigator.for_display()

        ## Exit if there's no change
        if msg == self.old_msg:
            return None

        ## Make the actual animation
        self.old_msg = msg
        return animationlib.MarqueeAnimation.fromText(msg, 16, freeze=True)

    def menu_exit(self):
        """Handle an exit request from the user"""
        print("User requested exit from menu")
        self.should_exit = True
        self.active = False
        self.watcher.reset()
        self.navigator.reset()

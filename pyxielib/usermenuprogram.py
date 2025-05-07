import pyxielib.animation_library as animationlib
from pyxielib import menu_library as menulib
from pyxielib.animation import Animation
from pyxielib.key_watcher import KeyWatcher
from pyxielib.navigator import Menu, Navigator
from pyxielib.program import Program
from pyxielib.tube_manager import cmdLen


class UserMenuProgram(Program):
    def __init__(self, event_path, *, program_map=None, ani_path='animations', **kwargs):
        super().__init__("User Control", **kwargs)
        self.event_path       = event_path
        self.program_map      = program_map or {}
        self.active           = False
        self.old_msg          = None
        self.should_exit      = False
        self.should_interrupt = False
        self.watcher = KeyWatcher(
            self.event_path,
            owner=self,
            hold=False,
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
            menulib.ProgramListItem(self.program_map),
            menulib.MirrorItem("Mirror Mode"),
            menulib.AnimationLibraryItem(ani_path),
            menulib.IpItem(),
            menulib.ExitItem("Exit Program"),
            menulib.WiFiMenu(),
            menulib.RebootItem(),
            menulib.ShutdownItem(),
        ]))

    def reset(self):
        """Reset the key watcher and user menu"""
        super().reset()
        self.navigator.reset()
        self.watcher.reset()
        self.active           = False
        self.old_msg          = None
        self.should_exit      = False
        self.should_interrupt = False

    def interrupt(self) -> bool:
        """Returns true if active animations and programs should be interrupted to check the user menu"""
        return self.should_interrupt

    def wake(self):
        self.active = True
        self.should_interrupt = True

    def done(self) -> bool:
        return self.should_exit

    def menu_exit(self):
        """Handle an exit request from the user"""
        print("User requested exit from menu")
        self.should_exit = True
        self.active = False
        self.watcher.reset()
        self.navigator.reset()

    def makeAnimation(self) -> Animation:
        """Make the menu animation"""
        ## Check the key watcher
        key = None
        self.should_interrupt = True
        if self.watcher.can_pop() and not self.should_exit:
            self.active = True
            try:
                key = self.watcher.pop()
            except KeyboardInterrupt:
                self.menu_exit()
                return None

        ## Enter key into the navigator
        msg = None
        if key is not None:
            msg = self.navigator.key_entry(key)

        ## If the key watcher didn't return a key, check the
        ## menu for an update anyway
        if msg is None:
            if self.navigator.should_exit:
                self.menu_exit()
                return None

            msg = self.navigator.for_display()

        ## Return now if this is an animation
        if isinstance(msg, Animation):
            ## Allow animation to complete
            self.should_interrupt = False
            self.old_msg = None
            return msg

        ## Exit if there's no change
        if msg == self.old_msg:
            return None

        ## Process msg
        self.old_msg = msg

        ## Make the actual animation
        if self.navigator.crop and cmdLen(msg) > self.size:
            msg = msg[-16:]

        return animationlib.MarqueeAnimation.fromText(msg, self.size, freeze=True)

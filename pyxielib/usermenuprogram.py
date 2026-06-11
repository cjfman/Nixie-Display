import logging

import pyxielib.animation_library as animationlib
from pyxielib import menu_library as menulib
from pyxielib import tap_revolution_menu as trmenulib
from pyxielib.animation import Animation
from pyxielib.key_watcher import KeyWatcher, TerminalKeyWatcher
from pyxielib.navigator import DisabledItem, Menu, Navigator
from pyxielib.program import Program
from pyxielib.tap_revolution_config import TapRevolutionConfig
from pyxielib.tube_manager import cmdLen

logger = logging.getLogger(__name__)


class UserMenuProgram(Program):
    def __init__(self, event_path=None, *, program_map=None, ani_path='animations',
                 levels_path='levels', tap_config=None, controller=None,
                 test_sound='audio-test-signal', **kwargs):
        super().__init__("User Control", **kwargs)
        self.event_path       = event_path
        self.program_map      = program_map or {}
        self.controller       = controller
        ## Fall back to code defaults (no persistence) when no config was supplied.
        tap_config            = tap_config or TapRevolutionConfig()
        self.active           = False
        self.old_msg          = None
        self.should_exit      = False
        self.should_interrupt = False
        if event_path is not None:
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
        else:
            self.watcher = TerminalKeyWatcher(owner=self)
        terminal_mode = (event_path is None)
        self.navigator = Navigator(Menu("Nixie Menu", [
            menulib.ProgramListItem(self.program_map),
            menulib.AnimationLibraryItem(ani_path),
            trmenulib.TapRevolutionMenu(tap_config, levels_path, watcher=self.watcher, size=self.size),
            menulib.MirrorItem("Mirror Mode"),
            menulib.AudioMenu(test_sound=test_sound),
            menulib.GitStatusItem(size=self.size),
            menulib.SleepItem(self.controller),
            menulib.ExitItem("Exit Program"),
            menulib.SystemInfoItem(),
            menulib.WiFiMenu(),
            menulib.RebootItem() if not terminal_mode else DisabledItem("Reboot"),
            menulib.ShutdownItem() if not terminal_mode else DisabledItem("Shutdown"),
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
        return self.should_interrupt or (self.active and self.watcher.can_pop())

    def wake(self):
        self.active = True
        self.should_interrupt = True
        if self.controller is not None:
            self.controller.enable()

    def done(self) -> bool:
        return self.should_exit

    def stop(self):
        self.watcher.stop()

    def menu_exit(self):
        """Handle an exit request from the user"""
        logger.info("User requested exit from menu")
        self.should_exit = True
        self.active = False
        self.watcher.reset()
        self.navigator.reset()

    def makeAnimation(self) -> Animation:
        """Make the menu animation"""
        ## Drain every key queued since the last poll. Processing the whole burst
        ## in one poll (rather than one key per ~100ms poll) keeps a rhythm game
        ## responsive; each key still carries its own capture time, so hit timing
        ## stays accurate even when several arrive together.
        msg = None
        self.should_interrupt = True
        while self.watcher.can_pop() and not self.should_exit:
            self.active = True
            try:
                key = self.watcher.pop()
            except KeyboardInterrupt:
                self.menu_exit()
                return None
            if key is not None:
                msg = self.navigator.key_entry(key)
            if self.navigator.should_exit:
                break

        ## If the key watcher didn't return a key, check the
        ## menu for an update anyway
        if msg is None:
            if self.navigator.should_exit:
                self.menu_exit()
                return None

            msg = self.navigator.for_display()

            ## A menu item may call set_done() from for_display() (e.g. a timed
            ## flash). Process the Navigator back-transition here so it fires on
            ## schedule without needing a keypress.
            if self.navigator.node.is_done():
                if not self.navigator.back():
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
        if msg == self.old_msg or msg is None:
            return None

        ## Process msg
        self.old_msg = msg

        ## Make the actual animation
        if self.navigator.crop and cmdLen(msg) > self.size:
            msg = msg[-16:]

        return animationlib.MarqueeAnimation.fromText(msg, self.size, freeze=True)

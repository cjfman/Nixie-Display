import logging
import subprocess
import time
from typing import List, Sequence, Tuple

from pyxielib.pyxieutil import PyxieError
from pyxielib.tube_manager import cmdLen

logger = logging.getLogger(__name__)


class MenuError(PyxieError):
    pass


class MenuItem:
    """An entry in a menu"""
    def __init__(self, name:str, parent=None, *, display_name=None, crop=False):
        self.name         = name
        self.parent       = parent
        self.display_name = display_name or name
        self.crop         = crop
        self.done         = False

    def for_display(self) -> str:
        """Return what should currently be displayed"""
        return self.name

    def is_done(self) -> bool:
        "Return true if this menu item is done"""
        return self.done

    def set_done(self):
        """Set done"""
        self.done = True

    def reset(self):
        """Reset menu item"""
        self.done = False

    def activate(self):
        """Should be called once when the menu item is activated"""
        ## Should be overridden

    def key_arrow(self, d):
        ## pylint: disable=unused-argument
        pass

    def key_up(self):
        self.key_arrow('U')

    def key_down(self):
        self.key_arrow('D')

    def key_right(self):
        self.key_arrow('R')

    def key_left(self):
        self.key_arrow('L')

    def key_enter(self):
        """Set done on enter"""
        self.set_done()

    def key_backspace(self):
        """Set done on backspace"""
        self.set_done()

    def key_esc(self):
        """Set done on ESC"""
        self.set_done()

    def key_char(self, c):
        """Handle a pressed key"""
        ## pylint: disable=unused-argument
        ## Should be overridden

    def key_ctrl_a(self):
        """Ctrl+A. Ignored unless overridden (e.g. move to line start)."""

    def key_ctrl_e(self):
        """Ctrl+E. Ignored unless overridden (e.g. move to line end)."""

    def __str__(self):
        return f"{self.__class__.__name__} '{self.name}'"

    def __repr__(self):
        return f"<{self}>"


class DisabledItem(MenuItem):
    """A menu item unavailable in the current context.
    Displays 'Disabled' when entered and exits back on any key press.
    """

    def for_display(self) -> str:
        return "Disabled"

    def key_char(self, c):
        self.set_done()

    def key_arrow(self, d):
        self.set_done()


class MsgItem(MenuItem):
    """
    A menu item that just displays a simple message.
    The message can be a function that returns a string
    """
    def __init__(self, name, msg, **kwargs):
        super().__init__(name, **kwargs)
        self.msg = msg

    def for_display(self) -> str:
        if callable(self.msg):
            return self.msg()

        return self.msg


class SubcommandItem(MenuItem):
    """
    Runs a subcommand and returns that to be displayed.
    When not blocking, 'for_display' should be called periodically as it could update
    """
    def __init__(self, name, cmd, *, shell=False, blocking=True, running_msg=None, **kwargs):
        super().__init__(name, **kwargs)
        self.cmd         = cmd
        self.shell       = shell
        self.blocking    = blocking
        self.running_msg = running_msg or "Running..."
        self.last_output = None
        self.proc        = None
        self.started     = False
        self.failed      = False

    def for_display(self) -> str:
        """
        Returns the result of the subprocess call.
        May return intermediate messages if non-blocking
        """
        if not self.blocking and self.proc is None:
            return "Not started"

        self.poll()
        if self.last_output is not None:
            return self.last_output
        if self.failed:
            return "Failed"

        return self.running_msg

    def poll(self):
        """Checks the output of a non-blocking subprocess call"""
        if self.last_output is not None:
            return

        ret = self.proc.poll()
        if ret is None:
            return
        if ret == 0:
            self.last_output = self.proc.stdout.decode('utf8')
        else:
            self.last_output = "Failed"

    def run(self):
        """Run the subprocess call. This might block"""
        if self.blocking:
            return subprocess.run(self.cmd, shell=self.shell, capture_output=True, check=False).stdout.decode('utf8')

        self.proc = subprocess.Popen(self.cmd, shell=self.shell, stdout=subprocess.PIPE)
        return self.proc

    def activate(self):
        res = self.run()
        if self.blocking:
            self.last_output = res

    def reset(self):
        MenuItem.reset(self)
        self.last_output = ""


class DelayedCommandItem(SubcommandItem):
    """
    Runs a subprocess after a specified amount of time.
    This will always be non-blocking
    """
    def __init__(self, name, cmd:str, delay=5, **kwargs):
        if not isinstance(cmd, str):
            raise ValueError("DelayedCommandItem can only use strings as cmd")
        if not isinstance(delay, int) or delay < 1:
            raise ValueError("DaylaedCommandItem delay must be a positive integer")

        cmd = f"sleep {delay} && " + cmd
        super().__init__(name, cmd, **kwargs, shell=True, blocking=False)


class ListItem(MenuItem):
    """A menu item that displays a list of strings"""
    def __init__(self, name, values=None, **kwargs):
        super().__init__(name, **kwargs)
        self.values = values or ["Empty List"]
        self.idx = 0

    def reset(self):
        super().reset()
        self.idx = 0

    def current_value(self) -> str:
        """Returns the string at the current index"""
        return self.values[self.idx]

    def for_display(self) -> str:
        return self.current_value()

    def set_values(self, values):
        """Set the values that should be displayed"""
        self.values = values or ["Empty List"]

    def key_down(self):
        """Go to the next item in the list. Will bottom out"""
        if self.idx + 1 < len(self.values):
            self.idx += 1
        else:
            self.idx = len(self.values) - 1

    def key_up(self):
        """Go to the previous item in the list"""
        if self.idx <= 0:
            self.idx = 0
        elif self.idx - 1 >= 0:
            self.idx -= 1

    def key_enter(self):
        """Specifically do nothing"""


_BLINK_PERIOD = 0.5   ## seconds per blink cycle
_BLINK_ON     = 0.25  ## seconds the value is underlined within each cycle


def _cycle_underline(s):
    return ''.join(c + '!' for c in s)


class CycleItem(MenuItem):
    """A setting that cycles through a fixed list of options, edited inline.

    Mirrors the Tap Revolution "difficulty" settings editor so the two can share
    one implementation. The parent menu shows the item's name plus its current
    value (e.g. "Difficulty Hard", "Mute OFF"); pressing Enter drops *straight*
    into edit mode — the value blinks with an underline and the arrow keys cycle
    through the options. Enter commits the highlighted option and pops back;
    ESC/Backspace cancels without changing anything.

    ``name`` is the title shown both in the parent menu (suffixed with the live
    value via the ``display_name`` property) and as the edit-mode prefix. In edit
    mode the "{name} | " prefix is dropped (leaving just the blinking value) when
    *any* option's "{name} | {label}" would overflow ``size`` tubes — so the name
    shows only when every option still fits, and never flickers mid-cycle.

    ``options`` may be a list of (value, label) pairs **or** a zero-argument
    callable that returns such a list; the callable form is re-evaluated on
    every ``activate()`` so the list stays fresh after config resets.
    """
    def __init__(self, name, options, get_fn, set_fn, *, size=16, **kwargs):
        super().__init__(name, **kwargs)
        self._options_src = options
        self._options = []
        self._get = get_fn
        self._set = set_fn
        self._size = size
        self._edit_idx = 0
        self._show_name = True

    @property
    def display_name(self) -> str:
        """Parent-menu label: the item name plus its current value (live)."""
        return f"{self.name} {self._current_label()}"

    @display_name.setter
    def display_name(self, value):
        ## Computed from name + current value; the base __init__ assignment
        ## (display_name or name) is intentionally ignored.
        pass

    def _resolve_options(self):
        return list(self._options_src() if callable(self._options_src) else self._options_src)

    def activate(self):
        ## Enter edit mode immediately, like the difficulty settings editor.
        self._options = self._resolve_options()
        self._edit_idx = self._current_idx()
        self._show_name = self._name_fits()

    def _name_fits(self) -> bool:
        """True only if "{name} | {label}" fits ``size`` for *every* option, so
        the name shows just when it always fits and never flickers mid-cycle."""
        prefix = f"{self.name} | "
        return all(cmdLen(prefix + label) <= self._size for _, label in self._options)

    def reset(self):
        super().reset()
        self._edit_idx = 0

    def _current_idx(self):
        val = self._get()
        for i, (v, _) in enumerate(self._options):
            if v == val:
                return i
        return 0

    def _current_label(self) -> str:
        ## Used by display_name, which may render before activate() populates
        ## _options, so fall back to resolving the source list here.
        options = self._options or self._resolve_options()
        val = self._get()
        for v, label in options:
            if v == val:
                return label
        return '?'

    def for_display(self) -> str:
        if not self._options:
            return self.name
        _, label = self._options[self._edit_idx]
        if time.time() % _BLINK_PERIOD < _BLINK_ON:
            label = _cycle_underline(label)
        if self._show_name:
            return f"{self.name} | {label}"
        return label

    def _cycle(self, delta):
        if self._options:
            self._edit_idx = (self._edit_idx + delta) % len(self._options)

    def key_enter(self):
        if self._options:
            value, label = self._options[self._edit_idx]
            if value != self._get():
                logger.info("Setting '%s' changed to %s", self.name, label)
            self._set(value)
        self.set_done()

    def key_esc(self):
        self.set_done()

    def key_backspace(self):
        self.set_done()

    def key_up(self):
        self._cycle(-1)

    def key_down(self):
        self._cycle(1)

    def key_left(self):
        self._cycle(-1)

    def key_right(self):
        self._cycle(1)


class Menu(MenuItem):
    """Displays a list of menu items that can be activated. Menus can be nested"""
    def __init__(self, name:str, items:Sequence[MenuItem]=None, **kwargs):
        super().__init__(name, **kwargs)
        self.items = list(items or tuple())
        self.parent = None
        self.idx = 0
        for item in self.items:
            if isinstance(item, Menu):
                item.parent = self

    def add_submenu(self, submenu):
        """Add a menu to be nested a submenu"""
        submenu.parent = self
        self.items.append(submenu)

    def for_display(self) -> str:
        """Display the current menu"""
        return self.items[self.idx].display_name

    def next(self) -> MenuItem:
        """Go to the next item"""
        if self.idx + 1 >= len(self.items):
            return None

        self.idx += 1
        return self.items[self.idx]

    def previous(self) -> MenuItem:
        """Go to the previous item"""
        if self.idx - 1 < 0:
            return None

        self.idx -= 1
        return self.items[self.idx]

    def current(self) -> MenuItem:
        """Get the current item"""
        if not self.items:
            raise MenuError("There are no menu items")

        return self.items[self.idx]

    def reset(self):
        MenuItem.reset(self)
        self.idx = 0
        for item in self.items:
            item.reset()

    def names(self) -> Tuple[str]:
        """Returns a tuple of all item's display names"""
        return tuple(x.display_name for x in self.items)

    def key_up(self):
        """Go to previous menu item"""
        self.previous()

    def key_down(self):
        """Go to next menu item"""
        self.next()

    def key_left(self):
        """Go back by setting done"""
        self.set_done()

    def key_backspace(self):
        """Go back by setting done"""
        self.set_done()

    def __str__(self):
        return f"{self.__class__.__name__} '{self.name}' idx={self.idx}"


class Navigator:
    """Manages a nested set of menus and menu items"""
    def __init__(self, root):
        self.root: Menu = root
        self.node: MenuItem = self.root
        self.visited: List[Menu] = []
        self.should_exit = False

        ## Populate methods with key suffixes
        for r in (range(ord('a'), ord('z') + 1), range(ord('A'), ord('Z') + 1), range(ord('0'), ord('9') + 1)):
            for i in r:
                c = chr(i)
                setattr(self, f"key_{c}", lambda cc=c: self.key_entry(cc))

        for key in ('down', 'up', 'left', 'right', 'enter', 'backspace'):
            key_u = key.upper()
            setattr(self, f"key_{key}", lambda k=key_u: self.key_entry(k))

    def for_display(self) -> str:
        """The current text that should be displayed"""
        return self.node.for_display()

    @property
    def crop(self):
        return self.node.crop

    def current_menu(self) -> Menu:
        """The current active menu. Could be any nested menu"""
        return self.node

    def current_item(self):
        """The current active menu item in the active menu"""
        return self.node.current()

    def reset(self):
        """Reset the navigator, all menues, and all menu items"""
        logger.trace("Navigator reset (full tree)")
        self.root.reset()
        self.node = self.root
        self.should_exit = False

    def enter(self):
        """Enter a menu or pass enter key to menu item"""
        if not isinstance(self.node, Menu):
            ## Pass enter key to menu item
            self.node.key_enter()
        else:
            ## Enter a menu
            self.visited.append(self.node)
            self.node = self.node.current()
            logger.trace("Activating node '%s'", self.node.name)
            self.node.activate()

    def back(self) -> bool:
        """
        Go to the previous menu.
        Return true if a previous menu has been activated.
        """
        if not self.visited:
            logger.trace("Navigator reset (exit at root)")
            self.root.reset()
            self.should_exit = True
            return False

        logger.trace("Resetting node '%s'", self.node.name)
        self.node.reset()
        self.node = self.visited.pop()
        return True

    def key_entry(self, key) -> str:
        """
        Handle a pressed key.
        If the menu item isn't done or a previous menu was activated,
        return the return value of for_display.
        Otherwise return None
        """
        if key == "BACKSPACE":
            self.node.key_backspace()
        elif key == "DOWN":
            self.node.key_down()
        elif key == "ENTER":
            ## Call on self
            self.enter()
        elif key == "ESC":
            self.node.key_esc()
        elif key == "LEFT":
            self.node.key_left()
        elif key == "RIGHT":
            self.node.key_right()
        elif key == "UP":
            self.node.key_up()
        elif key == "CTRL_A":
            self.node.key_ctrl_a()
        elif key == "CTRL_E":
            self.node.key_ctrl_e()
        elif len(key) == 1:
            self.node.key_char(key)

        ## Check if done
        if self.node.is_done():
            logger.debug(f'Node "{self.node.name}" is done')
            if not self.back():
                return None

        return self.for_display()

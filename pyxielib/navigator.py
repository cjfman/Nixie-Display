import subprocess
from typing import List, Sequence, Tuple

from pyxielib.pyxieutil import PyxieError


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

    def key_up(self):
        pass

    def key_down(self):
        pass

    def key_right(self):
        pass

    def key_left(self):
        pass

    def key_enter(self):
        """Set done on enter"""
        self.set_done()

    def key_backspace(self):
        """Set done on backspace"""
        self.set_done()

    def key_esc(self):
        """Set done on ESC"""
        self.set_done()

    def key_alpha_num(self, c):
        """Handle a pressed key"""
        ## pylint: disable=unused-argument
        ## Should be overridden

    def __str__(self):
        return f"{self.__class__.__name__} '{self.name}'"

    def __repr__(self):
        return f"<{self}>"


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
        self.values = ["Empty List"]
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


class MirrorItem(MenuItem):
    """Mirror whatever is typed. Exits on ESC and not backspace"""
    def __init__(self, name="Mirror", **kwargs):
        super().__init__(name, **kwargs, crop=True)
        self.msg = ""
        self._msg = ""
        self.bracket = False

    def for_display(self):
        return self.msg

    def reset(self):
        super().reset()
        self.msg = ""
        self._msg = ""

    def key_alpha_num(self, c):
        """Add a key to the message"""
        ## Check for a bracket, as this is a special character
        if c == '{':
            if self.bracket:
                ## We're already in bracket mode
                return

            self.bracket = True
        elif c == '}':
            if not self.bracket:
                ## We are not in bracket mode
                return

            self.bracket = False

        ## Add to the internal message and clone to external one
        self._msg += c
        self.msg = self._msg
        if self.bracket:
            ## Close bracket on external message
            self.msg += '}'

    def key_backspace(self):
        """Erase the last typed key"""
        if not self._msg:
            return

        if self._msg[-1] == '}':
            ## We've entered a bracket section
            self.bracket = True
        elif self._msg[-1] == '{':
            ## We've entered a bracket section
            self.bracket = False

        ## Add to the internal message and clone to external one
        self._msg = self._msg[:-1]
        self.msg = self._msg
        if self.bracket:
            ## Close bracket on external message
            self.msg += '}'


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
            self.node.activate()

    def back(self) -> bool:
        """
        Go to the previous menu.
        Return true if a previous menu has been activated.
        """
        if not self.visited:
            self.root.reset()
            self.should_exit = True
            return False

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
        elif len(key) == 1:
            self.node.key_alpha_num(key)

        ## Check if done
        if self.node.is_done():
            print(f'Node "{self.node.name}" is done')
            if not self.back():
                return None

        return self.for_display()

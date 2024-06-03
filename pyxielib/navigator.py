import subprocess
from typing import Sequence

from pyxielib.pyxieutil import PyxieError


class MenuError(PyxieError):
    pass


class MenuItem:
    def __init__(self, name:str, parent=None, *, display_name=None):
        self.name = name
        self.parent = parent
        self.display_name = display_name or name
        self.done = False

    def for_display(self):
        return self.name

    def is_done(self):
        return self.done

    def set_done(self):
        self.done = True

    def reset(self):
        self.done = False

    def on_active(self):
        pass

    def key_up(self):
        pass

    def key_down(self):
        pass

    def key_right(self):
        pass

    def key_left(self):
        pass

    def key_enter(self):
        self.set_done()

    def key_backspace(self):
        self.set_done()

    def key_esc(self):
        self.set_done()

    def key_alpha_num(self, c):
        ## pylint: disable=unused-argument
        pass

    def __str__(self):
        return f"{self.__class__.__name__} '{self.name}'"

    def __repr__(self):
        return f"<{self}>"


class MsgItem(MenuItem):
    def __init__(self, name, msg, **kwargs):
        super().__init__(name, **kwargs)
        self.msg = msg

    def for_display(self):
        if callable(self.msg):
            return self.msg()

        return self.msg


class SubcommandItem(MenuItem):
    def __init__(self, name, cmd, shell=False, **kwargs):
        super().__init__(name, **kwargs)
        self.cmd = cmd
        self.shell = shell
        self.last_output = ""

    def for_display(self):
        return self.last_output

    def run(self):
        return subprocess.run(self.cmd, shell=self.shell, capture_output=True, check=False).stdout.decode('utf8')

    def on_active(self):
        self.last_output = self.run()

    def reset(self):
        MenuItem.reset(self)
        self.last_output = ""


class ListItem(MenuItem):
    def __init__(self, name, values=None, **kwargs):
        super().__init__(name, **kwargs)
        self.values = values or ["Empty List"]
        self.idx = 0

    def reset(self):
        super().reset()
        self.values = ["Empty List"]
        self.idx = 0

    def current_value(self):
        return self.values[self.idx]

    def for_display(self):
        return self.current_value()

    def set_values(self, values):
        self.values = values or ["Empty List"]

    def key_down(self):
        if self.idx + 1 < len(self.values):
            self.idx += 1
        else:
            self.idx = len(self.values) - 1

    def key_up(self):
        if self.idx <= 0:
            self.idx = 0
        elif self.idx - 1 >= 0:
            self.idx -= 1

    def key_enter(self):
        pass


class Menu(MenuItem):
    def __init__(self, name:str, items:Sequence[MenuItem]=None, **kwargs):
        super().__init__(name, **kwargs)
        self.items = list(items or tuple())
        self.parent = None
        self.idx = 0
        for item in self.items:
            if isinstance(item, Menu):
                item.parent = self

    def add_submenu(self, submenu):
        submenu.parent = self
        self.items.append(submenu)

    def for_display(self):
        return self.items[self.idx].display_name

    def next(self):
        if self.idx + 1 >= len(self.items):
            return None

        self.idx += 1
        return self.items[self.idx]

    def previous(self):
        if self.idx - 1 < 0:
            return None

        self.idx -= 1
        return self.items[self.idx]

    def current(self):
        if not self.items:
            raise MenuError("There are no menu items")

        return self.items[self.idx]

    def reset(self):
        MenuItem.reset(self)
        self.idx = 0
        for item in self.items:
            item.reset()

    def names(self):
        return tuple(x.display_name for x in self.items)

    def key_up(self):
        self.previous()

    def key_down(self):
        self.next()

    def key_left(self):
        self.set_done()

    def key_backspace(self):
        self.set_done()

    def __str__(self):
        return f"{self.__class__.__name__} '{self.name}' idx={self.idx}"


class Navigator:
    def __init__(self, root):
        self.root = root
        self.node = self.root
        self.visited = []
        self.should_exit = False

    def for_display(self):
        return self.node.for_display()

    def current_menu(self):
        return self.node

    def current_item(self):
        return self.node.current()

    def reset(self):
        self.root.reset()
        self.node = self.root

    def enter(self):
        if not isinstance(self.node, Menu):
            raise MenuError("Cannot enter a non-menu item")

        self.visited.append(self.node)
        self.node = self.node.current()
        self.node.on_active()

    def back(self):
        if not self.visited:
            self.root.reset()
            self.should_exit = True
        else:
            self.node.reset()
            self.node = self.visited.pop()

    def next(self):
        return self.node.next()

    def previous(self):
        return self.node.previous()

    def key_enter(self):
        if not isinstance(self.node, Menu):
            self.node.key_enter()
        else:
            self.enter()

    def key_entry(self, key):
        if key == "BACKSPACE":
            self.node.key_backspace()
        elif key == "DOWN":
            self.node.key_down()
        elif key == "ENTER":
            ## Call on self
            self.key_enter()
        elif key == "ESC":
            ## Exit menu now
            self.reset()
            self.should_exit = True
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
            self.back()

        return self.for_display()

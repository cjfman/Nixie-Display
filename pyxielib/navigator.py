from typing import Sequence

from pyxielib.pyxieutil import PyxieError


class MenuError(PyxieError):
    pass


class MenuItem:
    def __init__(self, name:str, parent=None, *, display_name=None):
        self.name = name
        self.parent = parent
        self.display_name = display_name or name

    def for_display(self):
        return self.display_name

    def is_done(self):
        return True

    def reset(self):
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
        pass

    def key_backspace(self):
        pass


class Menu(MenuItem):
    def __init__(self, name:str, items:Sequence[MenuItem]=None, *args, **kwargs):
        MenuItem.__init__(self, name, *args, **kwargs)
        self.items = list(items or tuple())
        self.parent = None
        self.idx = 0
        self.done = False
        for item in self.items:
            if isinstance(item, Menu):
                item.parent = self

    def add_submenu(self, submenu):
        submenu.parent = self
        self.items.append(submenu)

    def for_display(self):
        return self.items[self.idx].display_name

    def is_done(self):
        return self.done

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
        self.idx = 0
        self.done = False
        for item in self.items:
            item.reset()

    def names(self):
        return tuple(x.display_name for x in self.items)

    def key_up(self):
        self.previous()

    def key_down(self):
        self.next()

    def key_left(self):
        self.done = True

    def key_backspace(self):
        self.done = True


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

    def enter(self):
        item = self.node.current()
        if not isinstance(item, Menu):
            raise MenuError("Cannot enter a non-menu item")

        self.visited.append(self.node)
        self.node = item

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
        item = self.node.current()
        if not isinstance(item, Menu):
            item.key_enter()
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
        elif key == "LEFT":
            self.node.key_left()
        elif key == "RIGHT":
            self.node.key_right()
        elif key == "UP":
            self.node.key_up()

        ## Check if done
        if self.node.is_done():
            self.back()

        return self.for_display()

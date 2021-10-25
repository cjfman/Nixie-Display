from pyxielib import decoder
from pyxielib import tube_manager as tm


class Controller:
    def __init__(self):
        pass

    def send(self, code):
        pass


class TerminalController(Controller):
    def __init__(self, *, clear_screen=False):
        Controller.__init__(self)
        self.clear_screen = clear_screen

    def clearScreen(self):
        print("\033[2J")

    def send(self, code):
        if self.clear_screen:
            self.clearScreen()

        print(decoder.bitmapsToDecodedStr(tm.cmdDecodePrint(code)))

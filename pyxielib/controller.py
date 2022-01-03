import serial

from pyxielib import decoder
from pyxielib import tube_manager as tm
from pyxielib.pyxieutil import PyxieError


class ControllerError(PyxieError):
    pass


class Controller:
    def __init__(self):
        pass

    def send(self, code):
        pass


class TerminalController(Controller):
    def __init__(self, *, clear_screen=False, print_code=False):
        Controller.__init__(self)
        self.clear_screen = clear_screen
        self.print_code   = print_code

    @staticmethod
    def clearScreen():
        print("\033[2J")

    def send(self, code):
        if self.clear_screen:
            self.clearScreen()

        if self.print_code:
            print(f"Print: '{code}'")
        else:
            print(decoder.bitmapsToDecodedStr(tm.cmdDecodePrint(code)))


class SerialController(Controller):
    def __init__(self, port:str, *, baud:int=9600, timeout:int=5, endl="\n\r", debug=False):
        Controller.__init__(self)
        self.port      = port
        self.baud      = baud
        self.timeout   = timeout
        self.endl      = endl
        self.debug     = debug
        self.serial    = serial.Serial(self.port, self.baud, timeout=self.timeout)
        self.on_prompt = False
        self.prompt    = '> '

        ## Check for header
        header = self.readline()
        if header != 'Nixie tube command terminal':
            raise ControllerError("Didn't find 'nixie tube' control board header")

    def opened(self) -> bool:
        return self.serial.is_open

    def open(self):
        if not self.opened():
            self.serial.open()

    def close(self):
        self.serial.close()

    def reset(self):
        self.close()
        self.serial.reset_input_buffer()
        self.serial.reset_output_buffer()
        self.open()

    def readline(self) -> str:
        if not self.opened():
            return ''

        line = self.serial.readline().decode('utf8')
        if line == self.prompt:
            self.on_prompt = True
            return ''

        self.on_prompt = False
        return line.strip()

    def findPrompt(self) -> bool:
        if self.on_prompt:
            return True

        line = self.serial.read_until(self.prompt.encode('utf8'))
        if self.debug:
            print(f"Read '{line}'")

        self.on_prompt = (len(line) > 0)
        return self.on_prompt

    def send(self, code):
        if not self.findPrompt():
            self.close()
            raise ControllerError("Can't find Nixie controller prompt")

        if self.debug:
            print(f"Command '{code}'")

        msg = f"print:{code}{self.endl}"
        self.serial.write(msg.encode('utf8'))
        self.serial.flush()
        self.on_prompt = False

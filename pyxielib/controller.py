import serial

from pyxielib import decoder
from pyxielib import tube_manager as tm
from pyxielib.pyxieutil import PyxieError


USE_RASPI=False
try:
    import spidev           ## pylint: disable=import-error
    import RPi.GPIO as GPIO ## pylint: disable=import-error
    GPIO.setmode(GPIO.BOARD)
    USE_RASPI=True
except:
    print("Warn: There is no 'spidev' library. The 'RaspberryPi' controller will not work")
    USE_RASPI=False


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
    def __init__(self, port:str, *, baud:int=115200, timeout:int=5, endl="\n\r", debug=False):
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
        line = self.readline()
        if self.debug:
            print(f"Read '{line}'")


class RaspberryPiController(Controller):
    def __init__(self, *, num_tubes=16, oe_pin=29, hv_pin=13, strobe_pin=15, \
            spi_ctrl=0, device=0, mode=2, speed=100000, \
            debug=False, print_code=False, cleanup=True):
        """Controller for directly using the RasPis output pins"""
        if not USE_RASPI:
            raise ControllerError("Cannot instantiate a 'RasbperryPiController'")

        Controller.__init__(self)
        self.num_tubes  = num_tubes
        self.oe_pin     = oe_pin
        self.hv_pin     = hv_pin
        self.strobe_pin = strobe_pin
        self.spi_ctrl   = spi_ctrl
        self.device     = device
        self.mode       = mode
        self.speed      = speed
        self.debug      = debug
        self.print_code = print_code
        self.cleanup    = cleanup
        self.spi        = None

        ## Setup GPIO
        GPIO.setwarnings(False)
        GPIO.setup(self.oe_pin, GPIO.OUT)
        GPIO.output(self.oe_pin, False)       ## Disable output
        GPIO.setup(self.strobe_pin, GPIO.OUT)
        GPIO.output(self.strobe_pin, True)    ## Disable strobe
        GPIO.setup(self.hv_pin, GPIO.OUT)
        GPIO.output(self.hv_pin, True)        ## Enable high voltage

        ## Setup SPI controller
        self.spi = spidev.SpiDev()
        self.spi.open(self.spi_ctrl, device)
        self.spi.max_speed_hz = self.speed
        self.spi.mode = self.mode

        self.enable()

    def enable(self):
        try:
            GPIO.output(self.oe_pin, True)     ## Disable strobe
            GPIO.output(self.strobe_pin, True) ## Disable strobe
        except Exception as e:
            msg = "Failed to enable display"
            if self.debug:
                msg += "\n\t" + str(e)

            raise ControllerError(msg)

    def disable(self):
        try:
            GPIO.output(self.oe_pin, False)
        except Exception as e:
            msg = "Failed to disable display"
            if self.debug:
                msg += "\n\t" + str(e)

            raise ControllerError(msg)

    def send(self, code):
        """Decode and send bitmaps over SPI"""
        if self.print_code:
            print(f"Command '{code}'")

        ## Ignore all errors
        try:
            self.spiSend(code)
        except:
            pass

    def spiSend(self, code):
        bitmaps = tm.cmdDecodePrint(code)

        ## Correct number of tubes
        if len(bitmaps) > self.num_tubes:
            data = bitmaps[:16] ## Take left most 16 tubes
        elif len(bitmaps) < self.num_tubes:
            missing = 16 - len(bitmaps)
            bitmaps.extend([0]*missing)

        data = []
        ## Send the data in reverse order
        for bitmap in reversed(bitmaps):
            msb = (bitmap >> 8) & 0xFF
            lsb = bitmap & 0xFF
            data += [msb, lsb]

        self.disable()
        self.spi.xfer(data)
        self.enable()

    def __del__(self):
        self.spi.close()
        if self.cleanup:
            GPIO.cleanup()

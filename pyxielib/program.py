import datetime
import threading
import time
import traceback

import pyxielib.animation as animation
from pyxielib.assembler import Assembler
from pyxielib.animation import Animation


class Program:
    def __init__(self, name, assembler:Assembler, delay: float=1):
        self.name         = name
        self.assembler    = assembler
        self.delay: float = delay
        self.running      = False
        self.shutdown     = False
        self.thread       = threading.Thread(target=self.handler)
        self.lock         = threading.Lock()
        self.cv           = threading.Condition(lock=self.lock)
        self.old_animation: Animation = None

    def isRunning(self):
        return self.running

    def isShutdown(self):
        return (self.shutdown or self.thread.is_alive())

    def run(self):
        if self.isRunning():
            return

        self.running = True
        self.thread.start()

    def stop(self):
        if not self.running:
            return

        self.running = False
        self.cv.acquire()
        self.cv.notify_all()
        self.cv.release()
        self.thread.join()
        self.shutdown = True

    def getAnimation(self):
        return self.old_animation

    def handler(self):
        self.cv.acquire()
        print(f"Starting {self.name} program thread")
        try:
            while self.running:
                new_animation = self.getAnimation()
                if self.old_animation != new_animation:
                    self.assembler.setAnimation(new_animation)
                    self.old_animation = new_animation

                self.cv.wait(self.delay)
        except Exception as e:
            print(f"Fatal error in {self.name} program: ", e)
            traceback.print_exc()

        self.cv.release()
        print(f"Exiting {self.name} program thread")
        self.shutdown = True

    def __del__(self):
        self.stop()


class ClockProgram(Program):
    def __init__(self, *args, use_24h=False, full_date=False, flash=False, **kwargs):
        super().__init__("Clock", *args, delay=0.1, **kwargs)
        self.full_date = full_date
        self.use_24h = use_24h
        self.hour_code = "%I"
        self.flash = flash
        self.am_pm_code = " %p"
        if use_24h:
            self.hour_code = "%k"
            self.am_pm_code = ''

    def getAnimation(self):
        code = self.getTimeCode()
        codes = [code, code.replace(':', '')]
        return animation.makeTextSequence(codes, 1, looped=True)

    def getTimeCode(self):
        if self.full_date:
            return self.fullDate()

        return self.shortDate()

    @staticmethod
    def formatDate(dateformat, dt=None):
        if dt is None:
            dt = datetime.datetime.now()
        return dt.strftime(dateformat)

    def shortDate(self):
        return self.formatDate(f"%a %d {self.hour_code}:%M")

    def fullDate(self):
        return self.formatDate(f"%A, %d %B %Y {self.hour_code}:%M{self.am_pm_code}")

    def timeOnly(self):
        return self.formatDate(f"{self.hour_code}:%M:%S{self.am_pm_code}")

    def dateAsText(self):
        return self.formatDate(f"%d %B")

    def dateTimeAsNumbers(self):
        return self.formatDate(f"%d/%m {self.hour_code}:%M:%S")

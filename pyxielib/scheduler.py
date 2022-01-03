import time
import threading
import traceback
from typing import Sequence, Tuple

from croniter import croniter

from pyxielib.assembler import Assembler
from pyxielib.program import Program
from pyxielib.pyxieutil import PyxieError


class SchedulerError(PyxieError):
    pass


TimeCode = str
ScheduleEntry = Tuple[TimeCode, Program]
TimeSlot = Tuple[float, Program]


class Scheduler:
    def __init__(self, assembler:Assembler, schedule:Sequence[ScheduleEntry], period:float=.1):
        self.assembler = assembler
        self.schedule  = list(schedule) ## Make copy
        self.period    = period
        self.program   = None
        self.running   = False
        self.shutdown  = False
        self.thread    = threading.Thread(target=self.handler)
        self.lock      = threading.Lock()
        self.cv        = threading.Condition(lock=self.lock)
        self.printSchedule()

    def isRunning(self):
        return (self.running and self.thread.is_alive())

    def isShutdown(self):
        return self.shutdown

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

    def currentProgram(self):
        return self.program

    def printSchedule(self):
        now = time.time()
        next_progs = [(croniter(tc, now).get_next(), prog) for tc, prog in self.schedule]
        next_progs = sorted(next_progs, key=lambda x: x[0])
        print(next_progs)

    def nextScheduledEntry(self) -> TimeSlot:
        if not self.schedule:
            raise SchedulerError("There are no programs. Cannot get next one")

        ## Check for only one entry
        if len(self.schedule) == 1:
            return (0, self.schedule[0][1])

        now = time.time()
        next_progs = [(croniter(tc, now).get_next(), prog) for tc, prog in self.schedule]
        next_prog = sorted(next_progs, key=lambda x: x[0])[0]
        return next_prog

    def checkSchedule(self):
        timecode, next_prog = self.nextScheduledEntry()
        name = next_prog.getName()
        now = time.time()
        ## Set program now if nothing else is running
        if self.program is None:
            self.program = next_prog
            print(f"Starting with program '{name}'")
            self.program.reset()
        ## Update program if it's scheduled to run now
        elif timecode <= now and next_prog != self.program:
            print(f"Switch to program '{name}'")
            self.program = next_prog
            self.program.reset()


    def pollProgram(self):
        if self.program and self.program.update():
            self.assembler.setAnimation(self.program.getAnimation())

    def handler(self):
        self.cv.acquire()
        print("Starting scheduler thread")
        try:
            while self.running:
                self.checkSchedule()
                self.pollProgram()
                self.cv.wait(self.period)
        except Exception as e:
            print("Fatal error in scheduler thread: ", e)
            traceback.print_exc()

        self.cv.release()
        print("Exiting scheduler thread")
        self.shutdown = True

    def __del__(self):
        self.stop()

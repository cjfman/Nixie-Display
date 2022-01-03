import datetime
import time
import threading
import traceback
from typing import Sequence, Tuple

from croniter import croniter

from pyxielib.assembler import Assembler
from pyxielib.program import Program
from pyxielib.pyxieutil import PyxieError, PyxieUnimplementedError


class SchedulerError(PyxieError):
    pass


CronTimeCode = str
ScheduleEntry = Tuple[CronTimeCode, Program]
TimeSlot = Tuple[float, Program]


def timeSlotToStr(slot:TimeSlot):
    ts, prog = slot
    dt = datetime.datetime.fromtimestamp(ts)
    return dt.strftime(f"%d/%m %H:%M:%S") + " : " + prog.getName()


class Scheduler:
    def __init__(self, assembler:Assembler, *, period:float=.1):
        self.assembler = assembler
        self.period    = period
        self.running   = False
        self.shutdown  = False
        self.thread    = threading.Thread(target=self.handler)
        self.lock      = threading.Lock()
        self.cv        = threading.Condition(lock=self.lock)

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

    def getProgram(self):
        raise PyxieUnimplementedError(self)

    def nextScheduledEntry(self) -> TimeSlot:
        raise PyxieUnimplementedError(self)

    def checkSchedule(self):
        raise PyxieUnimplementedError(self)

    def pollProgram(self):
        program = self.getProgram()
        if program and program.update():
            self.assembler.setAnimation(program.getAnimation())

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


class CronScheduler(Scheduler):
    def __init__(self, schedule:Sequence[ScheduleEntry], *args, **kwargs):
        Scheduler.__init__(self, *args, **kwargs)
        self.schedule = list(schedule) ## Make copy
        self.program  = None
        self.printSchedule()

    def getProgram(self):
        return self.program

    def printSchedule(self):
        now = time.time()
        next_progs = [(croniter(tc, now).get_next(), prog) for tc, prog in self.schedule]
        next_progs = sorted(next_progs, key=lambda x: x[0])
        print("Cron Program Schedule")
        for slot in next_progs:
            print(timeSlotToStr(slot))

    def nextScheduledEntry(self) -> TimeSlot:
        if not self.schedule:
            raise SchedulerError("There are no programs. Cannot get next one")

        ## Check for only one entry
        if len(self.schedule) == 1:
            return (0, self.schedule[0][1])

        now = time.time()
        slots = [(croniter(tc, now).get_next(), prog) for tc, prog in self.schedule]
        slot = sorted(slots, key=lambda x: x[0])[0]
        return slot

    def checkSchedule(self):
        slot = self.nextScheduledEntry()
        timestamp, next_prog = slot
        name = next_prog.getName()
        now = time.time() + 1 ## Ugly hack. Don't miss start of time slot
        ## Set program now if nothing else is running
        if self.program is None:
            self.program = next_prog
            print(f"Starting with program '{name}'")
            self.program.reset()
        ## Update program if it's scheduled to run now
        elif timestamp <= now and next_prog != self.program:
            print(f"Switch to program '{name}'")
            self.program = next_prog
            self.program.reset()

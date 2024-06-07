import datetime
import time
import threading
import traceback
from typing import List, Sequence

from croniter import croniter

from pyxielib.assembler import Assembler
from pyxielib.program import Program
from pyxielib.pyxieutil import PyxieError, PyxieUnimplementedError
from pyxielib.usermenuprogram import UserMenuProgram


class SchedulerError(PyxieError):
    pass


CronTimeCode = str

class TimeSlot:
    def __init__(self, timestamp:float, program:Program, priority:int=1):
        self.timestamp = timestamp
        self.program   = program
        self.priority  = priority

    def __lt__(self, other):
        if self.timestamp != other.timestamp:
            return (self.timestamp < other.timestamp)
        if self.priority != other.priority:
            return (self.priority > other.priority)

        return True

    def __str__(self):
        dt = datetime.datetime.fromtimestamp(self.timestamp)
        return dt.strftime(f"%d/%m %H:%M:%S") + " : " + self.program.getName()


class ScheduleEntry:
    def __init__(self, timecode:CronTimeCode, priority:int, program:Program):
        self.timecode = timecode
        self.priority = priority
        self.program  = program

    def nextTimeStamp(self, now=None) -> float:
        """Returns the timestamp of the next event"""
        if now is None:
            now = time.time()

        return croniter(self.timecode, now).get_next()

    def nextTimeStamps(self, n, now=None) -> List[float]:
        """Returns list of the timestamps of the next n events"""
        if now is None:
            now = time.time()

        return [croniter(self.timecode, now).get_next() for x in range(n)]

    def nextTimeSlot(self, now=None) -> TimeSlot:
        if now is None:
            now = time.time()

        return TimeSlot(self.nextTimeStamp(now), self.program, self.priority)


class Scheduler:
    def __init__(self, assembler:Assembler, *, period:float=.1, user_menu:UserMenuProgram=None):
        self.assembler = assembler
        self.period    = period
        self.user_menu = user_menu
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
        """Return True if a new event should be started"""
        raise PyxieUnimplementedError(self)

    def idle(self):
        """This is called when the current animation and program are done"""

    def pollProgram(self):
        program = None
        if self.user_menu is not None and self.user_menu.interrupt():
            program = self.user_menu
        else:
            program = self.getProgram()

        if program is None:
            return
        if program.update():
            ani = program.getAnimation()
            if ani is not None:
                self.assembler.setAnimation(ani)
        elif program.done():
            program.reset()
            self.idle()

    def handler(self):
        self.cv.acquire()
        print("Starting scheduler thread")
        try:
            while self.running:
                if self.checkSchedule() or self.assembler.animationDone():
                    try:
                        self.pollProgram()
                    except Exception as e:
                        print("Failed to poll program: ", e)
                        traceback.print_exc()
                        self.idle()

                self.cv.wait(self.period)
        except Exception as e:
            print("Fatal error in scheduler thread: ", e)
            traceback.print_exc()

        self.cv.release()
        print("Exiting scheduler thread")
        self.shutdown = True

    def __del__(self):
        self.stop()


class SingleProgramScheduler(Scheduler):
    def __init__(self, program:Program, *args, **kwargs):
        Scheduler.__init__(self, *args, **kwargs)
        self.program = program

    def getProgram(self):
        return self.program

    def nextScheduledEntry(self) -> TimeSlot:
        return (time.time(), self.program)

    def checkSchedule(self):
        pass


class CronScheduler(Scheduler):
    def __init__(self, schedule:Sequence[ScheduleEntry], *args, default=None, **kwargs):
        Scheduler.__init__(self, *args, **kwargs)
        self.schedule = [ScheduleEntry(*x) for x in (schedule or [])]
        self.program  = None
        self.default  = default
        self.slot     = None
        self.printSchedule()

    def getProgram(self):
        return self.program

    def printSchedule(self):
        now = time.time()
        print("Cron Program Schedule")
        slots = sorted([entry.nextTimeSlot(now) for entry in self.schedule])
        for slot in slots:
            print(str(slot))

    def nextScheduledEntry(self) -> TimeSlot:
        if not self.schedule:
            raise SchedulerError("There are no programs. Cannot get next one")

        ## Check for only one entry
        if len(self.schedule) == 1:
            return self.schedule[0].nextTimeSlot()

        now = time.time()
        slots = sorted([entry.nextTimeSlot(now) for entry in self.schedule])
        return slots[0]

    def checkSchedule(self):
        """Return True if a new event should be started"""
        slot = self.nextScheduledEntry()
        name = slot.program.getName()
        now = time.time() + 1 ## Ugly hack. Don't miss start of time slot
        ## Set program now if nothing else is running
        if self.program is None:
            self.program = slot.program
            print(f"Starting with program '{name}'")
            self.program.reset()
            return True
        ## Update program if it's scheduled to run now
        if slot.timestamp <= now and slot != self.slot and slot.program != self.program:
            print(f"Switch to program '{name}'")
            self.program = slot.program
            self.slot = slot
            self.program.reset()
            return True

        return False

    def idle(self):
        self.program = self.default

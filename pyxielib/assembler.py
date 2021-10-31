import threading

from pyxielib.controller import TerminalController

class Assembler:
    def __init__(self, *, controller=None, animation=None):
        self.running    = False
        self.shutdown   = False
        self.thread     = threading.Thread(target=self.handler)
        self.lock       = threading.Lock()
        self.cv         = threading.Condition(lock=self.lock)
        self.animation  = animation
        self.controller = controller or TerminalController()

    def isRunning(self):
        return self.running

    def isShutdown(self):
        return self.shutdown

    def setAnimation(self, animation):
        self.cv.acquire()
        self.animation = animation
        self.animation.resetTime()
        self.cv.notify_all()
        self.cv.release()

    def handler(self):
        self.cv.acquire()
        print("Starting assembler thread")
        while self.running:
            if self.animation is not None and self.animation.updateFrameSet():
                self.controller.send(self.animation.getCode())

            self.cv.wait(0.1)

        self.cv.release()
        print("Exiting assembler thread")

    def start(self):
        if self.animation is not None:
            self.animation.resetTime()

        self.running = True
        self.thread.start()

    def shutdown(self):
        self.running = False
        self.cv.notify_all()
        self.thread.join()
        self.shutdown = True

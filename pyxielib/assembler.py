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

    def rerun(self):
        self.cv.acquire()
        self.animation.resetTime()
        self.cv.notify_all()
        self.cv.release()

    def handler(self):
        self.cv.acquire()
        print("Starting assembler thread")
        while self.running:
            if self.animation is not None and self.animation.updateFrameSet():
                self.controller.send(self.animation.getCode())

            self.cv.wait(0.01)

        self.cv.release()
        print("Exiting assembler thread")

    def animationDone(self):
        return self.animation.done()

    def start(self):
        if self.animation is not None:
            self.animation.resetTime()

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

    def __del__(self):
        self.stop()

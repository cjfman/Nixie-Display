import threading
import traceback

from pyxielib.controller import Controller, TerminalController
from pyxielib.animation import DisplayAnimation

class Assembler:
    def __init__(self, *, controller: Controller=None, animation: DisplayAnimation=None):
        self.running    = False
        self.shutdown   = False
        self.thread     = threading.Thread(target=self.handler)
        self.lock       = threading.Lock()
        self.cv         = threading.Condition(lock=self.lock)
        self.animation : DisplayAnimation = animation
        self.controller: Controller   = controller or TerminalController()

    def isRunning(self):
        return (self.running and self.thread.is_alive())

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

    def doAnimation(self):
        if self.animation.updateFrameSet():
            self.controller.send(self.animation.getCode())

        if self.animation.done() and self.animation.shouldRepeat():
            self.animation.resetTime()

    def handler(self):
        self.cv.acquire()
        print("Starting assembler thread")
        try:
            while self.running:
                if self.animation:
                    self.doAnimation()

                self.cv.wait(0.01)
        except Exception as e:
            print("Fatal error in assembler thread: ", e)
            traceback.print_exc()

        self.cv.release()
        print("Exiting assembler thread")
        self.shutdown = True

    def animationDone(self):
        return self.animation.done()

    def start(self):
        if self.animation is not None:
            self.animation.resetTime()

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

    def __del__(self):
        self.stop()

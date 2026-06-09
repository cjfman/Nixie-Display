import logging
import threading
import traceback

from pyxielib.controller import Controller, TerminalController
from pyxielib.animation import Animation

logger = logging.getLogger(__name__)

class Assembler:
    ## How often the handler thread wakes to push the next frame. This is the
    ## animation frame-rate ceiling: frames whose delay is shorter than this are
    ## skipped (FullFrameAnimation.updateFrameSet keeps the timeline on schedule
    ## rather than stretching them). Smaller = smoother fast animations at the
    ## cost of more wake-ups; bounded in practice by controller.send() throughput.
    POLL_INTERVAL_SECS = 0.001

    def __init__(self, *, controller: Controller=None, animation: Animation=None, poll_interval=None):
        self.running    = False
        self.shutdown   = False
        self.poll_interval = poll_interval if poll_interval is not None else self.POLL_INTERVAL_SECS
        self.thread     = threading.Thread(target=self.handler)
        self.lock       = threading.Lock()
        self.cv         = threading.Condition(lock=self.lock)
        self.animation : Animation  = animation
        self.controller: Controller = controller or TerminalController()

    def isRunning(self):
        return (self.running and self.thread.is_alive())

    def isShutdown(self):
        return self.shutdown

    def setAnimation(self, animation):
        self.cv.acquire()
        self.animation = animation
        self.animation.reset()
        self.cv.notify_all()
        self.cv.release()

    def clearAnimation(self):
        self.animation = None

    def rerun(self):
        self.cv.acquire()
        self.animation.reset()
        self.cv.notify_all()
        self.cv.release()

    def handler(self):
        self.cv.acquire()
        logger.info("Starting assembler thread")
        try:
            while self.running:
                if self.animation and self.animation.updateFrameSet():
                    self.controller.send(self.animation.getCode())

                self.cv.wait(self.poll_interval)
        except Exception as e:
            logger.error(f"Fatal error in assembler thread: {e}")
            traceback.print_exc()

        self.cv.release()
        logger.info("Exiting assembler thread")
        self.shutdown = True

    def animationDone(self):
        self.cv.acquire()
        done = (self.animation is None or self.animation.done())
        self.cv.release()
        return done

    def start(self):
        if self.animation is not None:
            self.animation.reset()

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

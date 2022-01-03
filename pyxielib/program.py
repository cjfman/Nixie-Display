import datetime

import feedparser

import pyxielib.animation_library as animationlib
from pyxielib.animation import Animation, MarqueeAnimation
from pyxielib.pyxieutil import  PyxieUnimplementedError


class Program:
    def __init__(self, name, *args, **kwargs):
        self.name:str = name
        self.old_animation:Animation = None

    def getName(self):
        return self.name

    def getAnimation(self):
        raise PyxieUnimplementedError(self)

    def reset(self):
        self.old_animation = None

    def update(self):
        new_animation = self.getAnimation()
        if self.old_animation == new_animation:
            return False

        self.old_animation = new_animation
        return True

    def __str__(self):
        return f"{self.name}"

    def __repr__(self):
        return "<Program: " + str(self) + ">"


class ClockProgram(Program):
    def __init__(self, *args, use_24h=False, full_date=False, flash=False, **kwargs):
        super().__init__("Clock", *args, **kwargs)
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
        codes = [code]
        if self.flash:
            codes = [code, code.replace(':', '')]

        return animationlib.makeTextSequence(codes, 0.5, looped=True)

    def getTimeCode(self):
        if self.full_date:
            return self.fullDate()

        return self.date()

    @staticmethod
    def formatDate(dateformat, dt=None):
        if dt is None:
            dt = datetime.datetime.now()
        return dt.strftime(dateformat)

    def date(self):
        return self.formatDate(f"%a %d {self.hour_code}:%M:%S{self.am_pm_code}")

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


class RssProgram(Program):
    def __init__(self, url, *args, size:int=16, **kwargs):
        super().__init__(f"RSS {url}", *args, **kwargs)
        self.url       = url
        self.size      = size
        self.animation = None
        self.makeRssAnimation()

    def reset(self):
        self.animation = None
        self.makeRssAnimation()

    def makeRssAnimation(self):
        rss = feedparser.parse(self.url)
        msg = rss['feed']['title'] + " || " + (' '*(self.size//2)).join(map(lambda x: x['summary'], rss['entries']))
        self.animation = MarqueeAnimation.fromText(msg, self.size)

    def getAnimation(self):
        if self.animation.done():
            self.makeRssAnimation()

        return self.animation

import datetime

import feedparser

import pyxielib.animation_library as animationlib
from pyxielib.animation import Animation, MarqueeAnimation, escapeText
from pyxielib.pyxieutil import PyxieUnimplementedError, flattenHTML


class Program:
    def __init__(self, name):
        self.name:str = name
        self.old_animation:Animation = None

    def getName(self):
        return self.name

    def getAnimation(self):
        raise PyxieUnimplementedError(self)

    def reset(self):
        self.old_animation = None

    def done(self):
        return False

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
    def __init__(self, use_24h=False, full_date=False, flash=False):
        super().__init__("Clock")
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
        if self.flash:
            codes = [code, code.replace(':', '')]
            return animationlib.makeTextSequence(codes, 0.5, looped=True)

        return animationlib.makeTextSequence([code], 1)

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
    def __init__(self, url, *, name=None, size:int=16, max_entries=-1, \
            use_title=True, use_titles:bool=False, use_content=False, loop:bool=True
        ):
        super().__init__(name or f"RSS {url}")
        self.url         = url
        self.size        = size
        self.max_entries = max_entries
        self.use_title   = use_title
        self.use_titles  = use_titles
        self.use_content = use_content
        self.animation   = None
        self.loop        = loop

    def reset(self):
        Program.reset(self)
        self.animation = None

    def done(self):
        return (self.animation is not None and not self.loop)

    def escapeText(self, txt):
        return escapeText(txt)

    def makeRssAnimation(self):
        rss = feedparser.parse(self.url)
        entries = rss['entries'][:self.max_entries]
        values = []
        for entry in entries:
            ## Default is to sure the summary
            value = entry['summary']
            if self.use_content and 'content' in entry:
                ## Override summary with content
                value = flattenHTML(' '.join([x['value'] for x in entry['content']]))
            elif self.use_titles:
                ## Only add the title if the content isn't used
                value = entry['title'] + ": " + value

            values.append(value)

        msg = (' '*(self.size//2)).join(values)
        if self.use_title:
            msg = rss['feed']['title'] + " || " + msg

        self.animation = MarqueeAnimation.fromText(self.escapeText(msg), self.size)

    def getAnimation(self):
        if self.animation is None or (self.loop and self.animation.done()):
            self.makeRssAnimation()

        return self.animation


class WeatherProgram(RssProgram):
    def __init__(self, zipcode, size=16):
        self.zipcode = zipcode
        self.url = f"http://www.rssweather.com/zipcode/{self.zipcode}/rss.php"
        RssProgram.__init__(self, self.url,
            name='Weather', use_titles=True, use_content=True,
            size=size, max_entries=2, loop=False
        )

    def escapeText(self, txt):
        txt = RssProgram.escapeText(self, txt)
        replace = {
            'NORTH': 'N',
            'SOUTH': 'S',
            'EAST':  'E',
            'WEST':  'W',
            ' WIND DIRECTION:': '',
        }
        regex_rep = {
            r"< <\d+.>.>": '',
        }
        return escapeText(txt, replace, regex_rep)

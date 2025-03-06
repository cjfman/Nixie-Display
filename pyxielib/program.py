import datetime
import re
import traceback

import feedparser

import pyxielib.animation_library as animationlib
from pyxielib.animation import Animation, MarqueeAnimation, escapeText
from pyxielib.pyxieutil import PyxieUnimplementedError, flattenHTML


class Program:
    ## pylint: disable=no-self-use
    def __init__(self, name, size=16):
        self.name:str = name
        self.size:int = size
        self.old_animation:Animation = None
        self.failed = False

    def getName(self):
        return self.name

    def getAnimation(self) -> Animation:
        return self.old_animation

    def makeAnimation(self) -> Animation:
        raise PyxieUnimplementedError(self)

    def reset(self):
        self.old_animation = None
        self.failed = False

    def ready(self):
        return True

    def done(self):
        return (self.failed or self._done())

    def _done(self):
        return False

    def interrupt(self):
        return False

    def update(self):
        new_animation = None
        try:
            new_animation = self.makeAnimation()
        except Exception as e:
            print(f"Program '{self.name}' failed: {e}")
            traceback.print_exc()
            self.failed = True
            return False

        if new_animation is None or self.old_animation == new_animation:
            return False

        self.old_animation = new_animation
        return True

    def __str__(self):
        return f"{self.name}"

    def __repr__(self):
        return "<Program: " + str(self) + ">"


class ClockProgram(Program):
    def __init__(self, use_24h=False, full_date=False, flash=False, underscore=True):
        super().__init__("Clock")
        self.full_date  = full_date
        self.use_24h    = use_24h
        self.flash      = flash
        self.underscore = underscore
        self.hour_code  = "%I"
        self.am_pm_code = " %p"
        if use_24h:
            self.hour_code = "%k"
            self.am_pm_code = ''

    def makeAnimation(self):
        code = self.getTimeCode()
        colon = ':'
        if self.underscore:
            code = code.replace(':', '!')
            colon = '!'
            code = re.sub(r"(\d{2}!\d{2}!\d{2})", "\\1!", code)

        if self.flash:
            codes = [code, code.replace(colon, '')]
            return animationlib.makeTextSequence(codes, 0.5)

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
    def __init__(self, url, *, name=None, max_entries=-1, \
            use_title=True, use_titles:bool=False, use_content=False, loop:bool=False, **kwargs,
        ):
        super().__init__(name or f"RSS {url}", **kwargs)
        self.url         = url
        self.max_entries = max_entries
        self.use_title   = use_title
        self.use_titles  = use_titles
        self.use_content = use_content
        self.animation   = None
        self.loop        = loop
        self.banned_rx   = r"\b" + r"\b|\b".join(BANNED) + r"\b"


    def reset(self):
        super().reset()
        self.animation = None

    def _done(self):
        return (self.animation is not None and not self.loop)

    @staticmethod
    def escapeText(txt):
        return escapeText(txt)

    def makeRssAnimation(self):
        print(f"Querying RSS feed {self.url}")
        rss = feedparser.parse(self.url)
        entries = rss['entries'][:self.max_entries]
        values = []
        print(f"Found {len(entries)} entries")
        if not entries:
            self.animation = MarqueeAnimation.fromText("There is no weather", self.size)
            return

        for entry in entries:
            ## Check for banned terms
            ## Default is to sow the summary
            value = flattenHTML(entry['summary'])
            if self.use_content and 'content' in entry:
                ## Override summary with content
                value = flattenHTML(' '.join([x['value'] for x in entry['content']]))
            elif self.use_titles:
                ## Only add the title if the content isn't used
                value = entry['title'] + ": " + value

            if re.search(self.banned_rx, value, re.IGNORECASE) is None:
                values.append(value)

        msg = (' '*(self.size//2)).join(values) or "Nothing fit to print!"
        if self.use_title and 'title' in rss['feed']:
            msg = rss['feed']['title'] + " || " + msg

        self.animation = MarqueeAnimation.fromText(self.escapeText(msg), self.size)

    def makeAnimation(self):
        if self.animation is None or (self.loop and self.animation.done()):
            self.makeRssAnimation()

        return self.animation


class WeatherProgram(RssProgram):
    def __init__(self, *, zipcode=None, nws_code=None, url=None, **kwargs):
        self.zipcode  = zipcode
        self.nws_code = nws_code
        self.url      = None

        ## Set URL
        if url is not None:
            self.url = url
        elif self.zipcode is not None:
            self.url = f"http://www.rssweather.com/zipcode/{self.zipcode}/rss.php"
        elif self.nws_code is not None:
            self.url = f"https://forecast.weather.gov/xml/current_obs/{self.nws_code}.rss"
        else:
            raise ValueError("At least one of zipcode, nws_code, or url must be not None")

        RssProgram.__init__(self, self.url,
            name='Weather', use_titles=True, use_content=True,
            max_entries=2, loop=False, **kwargs,
        )

    def escapeText(self, txt):
        txt = RssProgram.escapeText(txt)
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

BANNED = (
    'trump',
    'musk',
    'donald',
    'elon',
    'united states',
    'putin',
    'russia',
    'president',
    'america',
)

import logging
import random
import os
import threading

from dataclasses import dataclass
from datetime import datetime

import requests
import bs4 as bs

import pyxielib.animation_library as animationlib
from pyxielib.animation import Animation, MarqueeAnimation
from pyxielib.program import Program

logger = logging.getLogger(__name__)

YAHOO_CHART_URL = 'https://query1.finance.yahoo.com/v8/finance/chart/{}'
YAHOO_CRUMB_URL = 'https://query2.finance.yahoo.com/v1/test/getcrumb'
YAHOO_COOKIE_URL = 'https://fc.yahoo.com/'
YAHOO_HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; nixie-display/1.0)'}

DEFAULT_SYMBOLS = ['AAPL', 'MGM']


@dataclass
class Stock:
    symbol: str
    current: float
    open:    float
    close:   float=None

    def __str__(self):
        try:
            diff = self.current - self.open
            perc = abs(diff / self.close * 100)
            sign = '+' if diff >= 0 else '-'
            return f"{self.symbol} {sign}{perc:.2f}%"
        except:
            price = self.current or 0
            return f"{self.symbol} ${price:.2f}"


def getSp500Symbols():
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; nixie-display/1.0)'}
        resp = requests.get('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies', headers=headers)
        soup = bs.BeautifulSoup(resp.text, 'html.parser')
        table = soup.find('table', {'id': 'constituents'})
        rows = table.findAll('tr') or []
        if rows:
            rows = rows[1:]

        tickers = []
        for row in rows:
            try:
                ticker = row.findAll('td')[0].text.strip()
                tickers.append(ticker)
            except Exception as e:
                logger.warning(f"Failed to parse symbol: {e}")

        if not tickers:
            logger.warning("Didn't find any S&P500 symbols")

        return sorted(tickers)
    except Exception as e:
        logger.error(f"Failed to get S&P500 symbols: {e}")
        return []


def fetchStock(symbol, session, crumb) -> Stock:
    """Fetch current quote for symbol from Yahoo Finance."""
    url = YAHOO_CHART_URL.format(symbol)
    resp = session.get(url, params={'interval': '1d', 'range': '1d', 'crumb': crumb})
    resp.raise_for_status()
    result = resp.json()['chart']['result'][0]
    meta = result['meta']
    quote = result['indicators']['quote'][0]
    current = meta['regularMarketPrice']
    open_price = (quote.get('open') or [None])[0]
    prev_close = meta['chartPreviousClose']
    return Stock(symbol, current, open_price, prev_close)


def isMarketOpen() -> bool:
    now = datetime.now()
    start = now.replace(hour=9, minute=30, second=0)
    end = now.replace(hour=16, second=0)
    return (start <= now < end)


class StockTicker(Program):
    def __init__(self, *, symbols=None, delay=5, quick_start=True):
        super().__init__("Stock Ticker")
        self.symbols      = None
        self.delay        = delay
        self.stocks       = {}
        self.started      = False
        self.running      = True
        self.shutdown     = False
        self.query_idx    = 0
        self.max_failures = 10
        self.quick_start  = quick_start
        self.session      = None
        self.crumb        = None
        self.thread       = threading.Thread(target=self.handler)
        self.lock         = threading.Lock()
        self.cv           = threading.Condition(lock=self.lock)

        ## Determine the list of stock symbols
        if isinstance(symbols, str):
            if os.path.isfile(symbols):
                raise ValueError("Loading stock symbols from file not supported")

            self.symbols = symbols.split(',')
        elif symbols is None:
            self.symbols = getSp500Symbols() or DEFAULT_SYMBOLS
        else:
            raise ValueError(f"Type {type(symbols)} not supported for stocks parameter")

    def _refreshSession(self):
        """Obtain a fresh Yahoo Finance session and crumb."""
        logger.info("Refreshing Yahoo Finance session")
        session = requests.Session()
        session.headers.update(YAHOO_HEADERS)
        session.get(YAHOO_COOKIE_URL)
        resp = session.get(YAHOO_CRUMB_URL)
        resp.raise_for_status()
        self.session = session
        self.crumb = resp.text.strip()

    def reset(self):
        super().reset()
        self.cv.acquire()
        self.query_idx = 0
        self.cv.release()

    def isRunning(self):
        return (self.running and self.thread.is_alive())

    def isShutdown(self):
        return self.shutdown

    def run(self):
        if not self.started:
            self.started = True
            self.thread.start()

    def stop(self):
        if not self.running:
            return

        logger.info("Stopping the StockTicker thread")
        self.running = False
        self.cv.acquire()
        self.cv.notify_all()
        self.cv.release()
        self.thread.join()
        self.shutdown = True

    def ready(self):
        return (self.running and self.stocks and isMarketOpen())

    def clearStocks(self):
        now = datetime.now()
        start = now.replace(hour=9, minute=30, second=0)
        if now < start and self.stocks:
            self.stocks = {}

    def makeAnimation(self) -> Animation:
        """Take a list of stocks and turn it into a marquee"""
        if not isMarketOpen():
            return animationlib.makeTextAnimation("Market closed", length=1)
        if not self.stocks:
            return MarqueeAnimation.fromText("No quotes loaded", size=self.size)

        self.cv.acquire()
        symbols = self.symbols[:]
        random.shuffle(symbols)
        quotes = [str(self.stocks[sym]) for sym in symbols if sym in self.stocks]
        self.cv.release()
        ani = MarqueeAnimation.fromText(" | ".join(quotes), size=self.size)
        return ani

    def handler(self):
        """The main stock loop"""
        logger.info("Stock ticker thread starting")
        failures = 0
        self.cv.acquire()
        while self.running:
            ## Don't run if the market isn't open
            if not isMarketOpen():
                self.clearStocks()
                self.cv.wait(1)
                continue

            ## Try and update all of the stocks
            try:
                if self.updateStocks():
                    failures = 0
                else:
                    failures += 1
            except Exception as e:
                logger.error(f"Failed to update stocks: {e}")
                failures += 1

            self.quick_start = False
            ## Increase wait time between cycles based on consecutive failures
            if self.running:
                self.cv.wait(2**failures)

        logger.info("Exiting stock ticker thread")
        self.cv.release()

    def updateStocks(self):
        """
        Get new stock information. Returns True if every stock was updated.
        Returns False on the first failure. When called again, picks up with
        the stock that was next after the failed one.
        """
        failures = 0
        delay = self.delay if not self.quick_start else 1
        if self.query_idx >= len(self.symbols):
            self.query_idx = 0

        if self.session is None:
            self._refreshSession()

        for sym in self.symbols[self.query_idx:]:
            if not self.running:
                return True

            self.query_idx += 1
            try:
                stock = fetchStock(sym, self.session, self.crumb)
                logger.debug(f"Got stock {stock}")
                self.stocks[sym] = stock
                self.cv.wait(delay)
            except Exception as e:
                if isinstance(e, requests.HTTPError) and e.response is not None \
                        and e.response.status_code in (401, 403):
                    logger.warning("Auth expired, refreshing session")
                    self._refreshSession()
                logger.warning(f"Failed to query stock {sym}: {e}")
                failures += 1
                if failures > self.max_failures:
                    logger.warning("Failed too many times. Reseting")
                    return False

        return True

    def __del__(self):
        self.stop()

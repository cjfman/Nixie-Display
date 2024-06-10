import random
import os
import time
import threading

from dataclasses import dataclass

import requests

import bs4 as bs
import yfinance as yf
from pyxielib.animation import Animation, MarqueeAnimation
from pyxielib.program import Program

DEFAULT_SYMBOLS = ['AAPL', 'MGM']
DEBUG = False


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
            return f"{self.symbol} {sign}${abs(diff):.2f}/{perc:.2f}%"
        except:
            return f"{self.symbol} ${self.current:.2f}"


def getSp500Symbols():
    try:
        resp = requests.get('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies')
        soup = bs.BeautifulSoup(resp.text, 'html.parser')
        table = soup.find('table', {'class': 'wikitable sortable'})
        tickers = []
        for row in table.findAll('tr')[1:]:
            ticker = row.findAll('td')[0].text.strip()
            tickers.append(ticker)

        return sorted(tickers)
    except Exception as e:
        print(f"Failed to get S&P500 symbols: {e}")
        return []


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
        self.shown_idx    = 0
        self.max_failures = 10
        self.quick_start  = quick_start
        self.thread       = threading.Thread(target=self.handler)
        self.lock         = threading.Lock()
        self.cv           = threading.Condition(lock=self.lock)

        ## Dedermin the list of stock symbols
        if isinstance(symbols, str):
            if os.path.isfile(symbols):
                ## Load from a file
                raise ValueError("Loading stock symbols from file not supported")

            self.symbols = symbols.split(',')
        elif symbols is None:
            self.symbols = getSp500Symbols() or DEFAULT_SYMBOLS
        else:
            raise ValueError(f"Type {type(symbols)} not supported for stocks parameter")

    def reset(self):
        super().reset()
        self.cv.acquire()
        self.query_idx = 0
        self.shown_idx = 0
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

        print("Stopping the StockTicker thread")
        self.running = False
        self.cv.acquire()
        self.cv.notify_all()
        self.cv.release()
        self.thread.join()
        self.shutdown = True

    def _done(self):
        return not self.stocks

    def makeAnimation(self) -> Animation:
        """Take a list of stocks and turn it into a marquee"""
        if not self.stocks:
            return None

        self.cv.acquire()
        quotes = []
        #for sym in self.symbols[self.shown_idx:]:
        symbols = self.symbols[:]
        random.shuffle(symbols)
        for sym in symbols:
            if sym in self.stocks:
                quotes.append(str(self.stocks[sym]))

        self.cv.release()
        ani = MarqueeAnimation.fromText(" | ".join(quotes), size=self.size)
        return ani

    def handler(self):
        """The main stock loop"""
        print("Stock ticker thread starting")
        failures = 0 ## Consecutive failures
        self.cv.acquire()
        while self.running:
            try:
                if self.updateStocks():
                    failures = 0
                else:
                    failures += 1
            except Exception as e:
                print(f"Failed to update stocks: {e}")
                failures += 1

            self.quick_start = False
            ## Increase wait time between cycles based on
            ## the number consecutive failures
            if self.running:
                self.cv.wait(2**failures)

        print("Exiting stock ticker thread")
        self.cv.release()

    def updateStocks(self):
        """
        Get new stock information. Returns True if every stock was updated
        Returns False on the first failure. When called again, pick up with stock
        that was next after the failed one.
        """
        failures = 0
        delay = self.delay if not self.quick_start else 1
        if self.query_idx >= len(self.symbols):
            self.query_idx = 0

        ## Start off from where we left off
        for sym in self.symbols[self.query_idx:]:
            if not self.running:
                return True

            self.query_idx += 1
            try:
                ## Get the current stock quotes
                ticker = yf.Ticker(sym)
                info = ticker.fast_info
                stock = Stock(sym, info['lastPrice'], info['open'], info['previousClose'])
                if DEBUG:
                    print(f"Got stock {stock}")

                ## Store the result
                self.stocks[sym] = stock
                self.cv.wait(delay)
            except Exception as e:
                print(f"Failed to query stock {sym}: {e}")
                failures += 1
                if failures > self.max_failures:
                    print(f"Failed too many times. Resting")
                    return False

        return True

    def __del__(self):
        self.stop()

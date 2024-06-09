import os
import requests
import threading

from dataclasses import dataclass

import bs4 as bs
import lxml
import pickle
import yfinance as yf
from pyxielib.animation import Animation, MarqueeAnimation
from pyxielib.program import Program

DEBUG = True
DEFAULT_SYMBOLS = ['AAPL', 'MGM']


@dataclass
class Stock:
    symbol: str
    current: float
    open:    float
    close:   float=None

    def __str__(self):
        diff = self.current - self.close
        perc = abs(diff / self.close * 100)
        return f"{self.symbol} ${diff:.2f}/{perc:.2f}%"


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
    def __init__(self, *, symbols=None, delay=5):
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
        self.thread       = threading.Thread(target=self.handler)
        self.lock         = threading.Lock()
        self.cv           = threading.Condition(lock=self.lock)

        if isinstance(symbols, str):
            if os.path.isfile(symbols):
                ## Load from a file
                ## XXX
                pass
            else:
                self.symbols = symbols.split(',')
        elif symbols is None:
            self.symbols = getSp500Symbols() or DEFAULT_SYMBOLS
        else:
            raise ValueError(f"Type {type(symbols)} not supported for stocks parameter")

        self.run()

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
        if self.running:
            return

        self.running = False
        self.cv.acquire()
        self.cv.notify_all()
        self.cv.release()
        self.thread.join()
        self.shutdown = True

    def _done(self):
        return (not self.stocks)

    def handler(self):
        print("Stock ticker thread starting")
        failures = 0
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

            self.cv.wait(2**failures)

        print("Stock ticker thread exiting")
        self.cv.release()

    def makeAnimation(self) -> Animation:
        if not self.stocks:
            return None

        quotes = []
        for sym in self.symbols[self.shown_idx:]:
            if sym in self.stocks:
                quotes.append(str(self.stocks[sym]))

        ani = MarqueeAnimation.fromText("  -  ".join(quotes), size=self.size)
        return ani

    def updateStocks(self):
        failures = 0
        if self.query_idx >= len(self.symbols):
            self.query_idx = 0

        for sym in self.symbols[self.query_idx:]:
            self.query_idx += 1
            try:
                ticker = yf.Ticker(sym)
                info = ticker.fast_info
                stock = Stock(sym, info['lastPrice'], info['open'], info['previousClose'])
                if DEBUG:
                    print(f"Got stock {stock}")

                self.stocks[sym] = stock
                self.cv.wait(self.delay)
            except Exception as e:
                print(f"Failed to query stock {sym}: {e}")
                failures += 1
                if failures > self.max_failures:
                    print(f"Failed too many times. Resting")
                    return False

        return True

    def __del__(self):
        self.stop()

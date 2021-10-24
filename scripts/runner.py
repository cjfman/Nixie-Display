#!/usr/bin/python3
import datetime
import sys
import thread
import time

import decoder as d
import tube_manager as tm


def clearScreen():
    print("\033[2J")


def demoPrint(msg):
    print(d.bitmapsToDecodedStr(tm.cmdDecodePrint(msg)))



def msgShortDate(dt=None):
    if dt is None:
        dt = datetime.datetime.now()
    return dt.strftime("%a %d %I:%M")


def msgFullDate(dt=None):
    if dt is None:
        dt = datetime.datetime.now()
    return dt.strftime("%A, %d %B %Y %I:%M%p")


def programDate(*args, **kwargs):
    return msgShortDate()


def runProgram(program, delay=10):
    program()
    time.sleep(delay)



def main():
    clearScreen()
    demoPrint("{!Hello}")
    time.sleep(3)
    clearScreen()
    demoPrint(msgShortDate())
    time.sleep(3)
    return 0

if __name__ == '__main__':
    sys.exit(main())

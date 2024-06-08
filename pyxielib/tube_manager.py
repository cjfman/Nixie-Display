import re
from typing import List

from pyxielib import decoder
from pyxielib.pyxieutil import PyxieError



class DecodeError(PyxieError):
    pass


def cmdDecodePrint(cmd) -> List[int]:
    ## pylint: disable=too-many-branches
    out = []
    token = ''
    state = 'start'
    for c in cmd:
        if state == 'start':
            ## First character must be printable or token start
            if c == '{':
                state = 'token_start'
                continue

            if not decoder.isPrintable(c):
                raise DecodeError("First character must be pritable or start a token")

            ## Decode and set char
            out.append(decoder.decodeChar(c))
            state = 'idle'
        elif state == 'idle':
            if c == '{':
                state = 'token_start'
            elif c == '!':
                out[-1] = decoder.underlineCode(out[-1])
                state = 'idle'
            elif c == ':':
                out[-1] = decoder.colonCode(out[-1])
                state = 'idle'
            else:
                ## Decode and set char
                out.append(decoder.decodeChar(c))
                state = 'idle'
        elif state == 'token_start':
            if c == '!':
                state = 'underline'
                continue

            token = ''
            state = 'token'

        ## Allow 'token_start' state to jump to 'token'
        if state == 'token':
            ## Add to token buf
            if c != '}':
                token += c
                state = 'token'
            else:
                out.append(cmdDecodeToken(token))
                state = 'idle'
        elif state == 'underline':
            if c == '}':
                state = 'idle'
            else:
                out.append(decoder.decodeAndUnderline(c))
                state = 'underline'

    return out


def cmdDecodeToken(token) -> int:
    """Turn a hex/bin token into an int"""
    if token[:2] in ("0x", "0X"):
        return int(token, 16)
    if token[:2] in ("0b", "0B"):
        return int(token, 2)

    raise DecodeError(f"Invalid token '{token}'")


def cmdLen(cmd) -> int:
    return len(re.sub(r"\{[^\}]*\}|!", '', cmd))

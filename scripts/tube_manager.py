import decoder

class DecodeError(Exception):
    pass


def cmdDecodePrint(cmd):
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
                continue

            out.append(cmdDecodeToken(token))
        elif state == 'underline':
            if c == '}':
                state = 'idle'
            else:
                out.append(decoder.decodeAndUnderline(c))
                state = 'underline'

    return out


def cmdDecodeToken(token):
    if token[:2] in ("0x", "0X"):
        return int(token, 16)
    elif token[:2] in ("0b", "0B"):
        return int(token, 2)

    raise DecodeError(f"Invalid token '{token}'")

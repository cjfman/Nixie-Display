NOCODE = 0x3FFF

## Array of ascii codes decoded into 14-segments
## Stats at ascii code 0x20. Ends at 0x7F inclusive
## TODO Fix V, v, $, 3
codes = (
    0x0000, 0x4880, 0x0082, NOCODE, ## ' ', !, ", #
    0x2AAD, 0x1124, NOCODE, 0x0100, ##    $, %, %, '
    NOCODE, NOCODE, 0x3FC0, 0x2A80, ##    (, ), *, +
    0x1000, 0x2200, 0x0010, 0x1100, ## ',', -, ., /
    0x113F, 0x0106, 0x221B, 0x030D, ##   0, 1, 2, 3
    0x2226, 0x2429, 0x223D, 0x1101, ##   4, 5, 6, 7
    0x223F, 0x2227, NOCODE, NOCODE, ##   8, 9, :, ;
    0x0500, NOCODE, 0x1040, NOCODE, ##   <, =, >, ?
    NOCODE, 0x1306, 0x0A8F, 0x0039, ##   @, A, B, C
    0x088F, 0x2039, 0x2031, 0x023D, ##   D, E, F, G
    0x2236, 0x0889, 0x1091, 0x0D80, ##   H, I, J, K
    0x0038, 0x0176, 0x0476, 0x003F, ##   L, M, N, O
    0x2233, 0x043F, 0x2633, 0x222D, ##   P, Q, R, S
    0x0881, 0x003E, 0x1130, 0x1436, ##   T, U, V, W
    0x1540, 0x0940, 0x1109, NOCODE, ##   X, Y, Z, [
    0x0440, NOCODE, 0x1400, 0x0008, ##   \, ], ^, _
    0x0040, 0x1306, 0x0A8F, 0x0039, ##   1, a, b, c
    0x088F, 0x2039, 0x2031, 0x023D, ##   d, e, f, g
    0x2236, 0x0889, 0x1091, 0x0D80, ##   h, i, j, k
    0x0038, 0x0176, 0x0476, 0x003F, ##   l, m, n, o
    0x2233, 0x043F, 0x2633, 0x222D, ##   p, q, r, s
    0x0881, 0x003E, 0x1130, 0x1436, ##   t, u, v, w
    0x1540, 0x0940, 0x1109, NOCODE, ##   x, y, z, {
    0x0880, NOCODE, NOCODE, NOCODE  ##   |, }, ~, 'DEL'
)

def decodeChar(c):
    c = ord(c)
    if c < 0x20 or 0x7f < c:
        return NOCODE ## Non-printable code

    return codes[c - 0x20]


##  ___   Line 0
## |\|/|  Line 1
## -- --  Line 2
## |/|\|  Line 3
##  ___   Line 4

def bitmapToLines(bitmap):
    lines = []

    ## Line 0
    line = " " * 5
    if bitmap & 0x0001:
        line = " ___ "
    lines.append(line)

    ## Line 1
    line = [" "] * 5
    if bitmap & 0x0020:
        line[0] = '|'
    if bitmap & 0x0040:
        line[1] = '\\'
    if bitmap & 0x0080:
        line[2] = '|'
    if bitmap & 0x0100:
        line[3] = '/'
    if bitmap & 0x0002:
        line[4] = '|'
    lines.append(''.join(line))

    ## Line 2
    line = [" "] * 5
    if bitmap & 0x2000:
        line[0] = '-'
        line[1] = '-'
    if bitmap & 0x0200:
        line[3] = '-'
        line[4] = '-'
    lines.append(''.join(line))

    ## Line 3
    line = [" "] * 5
    if bitmap & 0x0010:
        line[0] = '|'
    if bitmap & 0x1000:
        line[1] = '/'
    if bitmap & 0x0800:
        line[2] = '|'
    if bitmap & 0x0400:
        line[3]= '\\'
    if bitmap & 0x0004:
        line[4] = '|'
    lines.append(''.join(line))

    ## Line 4
    line = " " * 5
    if bitmap & 0x0008:
        line = " ___ "
    lines.append(line)

    return lines


def bitmapToDecodedChar(bitmap):
    return "\n".join(bitmapToLines(bitmap)) + "\n"

def strToDecodedStr(s):
    bitmaps = [decodeChar(c) for c in s]
    bitmap_lines = [bitmapToLines(b) for b in bitmaps]
    out_lines = [""] * 5
    for lines in bitmap_lines:
        for i, line in enumerate(lines):
            out_lines[i] += line + "    "

    for i, line in enumerate(out_lines):
        out_lines[i] += "\n"

    return "\n".join(out_lines) + "\n"


def printBitmap(bitmap):
    print(bitmapToStr(bitmap))


def printChar(c):
    print(bitmapToDecodedChar(decodeChar(c)))

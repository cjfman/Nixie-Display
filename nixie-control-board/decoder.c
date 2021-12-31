#include <inttypes.h>

#include "decoder.h"

// Array of ascii codes decoded into 14-segments
// Stats at ascii code 0x20. Ends at 0x7F inclusive
uint16_t codes[] = {
    0x0000, NOCODE, 0x0082, NOCODE, // ' ', !, ", #
    0x2AAD, 0x1124, NOCODE, 0x0100, //    $, %, %, '
    NOCODE, NOCODE, 0x3FC0, 0x2A80, //    (, ), *, +
    0x1000, 0x2200, 0x0010, 0x1100, // ',', -, ., /
    0x113F, 0x0106, 0x221B, 0x030D, //   0, 1, 2, 3
    0x2226, 0x2429, 0x223D, 0x1101, //   4, 5, 6, 7
    0x223F, 0x2227, NOCODE, NOCODE, //   8, 9, :, ;
    0x0500, NOCODE, 0x1040, NOCODE, //   <, =, >, ?
    NOCODE, 0x1306, 0x0A8F, 0x0039, //   @, A, B, C
    0x088F, 0x2039, 0x2031, 0x023D, //   D, E, F, G
    0x2236, 0x0889, 0x1091, 0x0D80, //   H, I, J, K
    0x0038, 0x0176, 0x0476, 0x003F, //   L, M, N, O
    0x2233, 0x043F, 0x2633, 0x222D, //   P, Q, R, S
    0x0881, 0x003E, 0x1130, 0x1436, //   T, U, V, W
    0x1540, 0x0940, 0x1109, NOCODE, //   X, Y, Z, [
    0x0440, NOCODE, 0x1400, 0x0008, //   \, ], ^, _
    0x0040, 0x1306, 0x0A8F, 0x0039, //   `, a, b, c
    0x088F, 0x2039, 0x2031, 0x023D, //   d, e, f, g
    0x2236, 0x0889, 0x1091, 0x0D80, //   h, i, j, k
    0x0038, 0x0176, 0x0476, 0x003F, //   l, m, n, o
    0x2233, 0x043F, 0x2633, 0x222D, //   p, q, r, s
    0x0881, 0x003E, 0x1130, 0x1436, //   t, u, v, w
    0x1540, 0x0940, 0x1109, NOCODE, //   x, y, z, {
    0x0880, NOCODE, NOCODE, NOCODE  //   |, }, ~, 'DEL'
};

uint16_t decodeChar(char c) {
    if (c < 0x20 || 0x7f < c) {
        return NOCODE; // Non-printable code
    }
    return codes[c - 0x20];
};

int isPrintable(char c) {
    return (decodeChar(c) != NOCODE);
}

uint16_t underlineCode(uint16_t code) {
    return code | 0x4000;
}

uint16_t colonCode(uint16_t code) {
    return code | 0x8000;
}

uint16_t decodeAndUnderline(char c) {
    return underlineCode(decodeChar(c));
}

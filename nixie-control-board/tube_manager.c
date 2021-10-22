#include <inttypes.h>
#include <stdlib.h>
#include <string.h>

#include "decoder.h"
#include "tube_manager.h"

// cmd prefixes
const char tube_cmd_print[] = "print";

// Ring buffer
char cmd_buf[CMD_BUF_SIZE];
int cmd_buf_len = 0;

void clearCache(void) {
    cmd_buf_len = 0;
}

int buildCmd(char* new_cmd, int len) {
    // Check size
    if (len + cmd_buf_len > CMD_BUF_SIZE - 1) {
        // Buffer overrun
        // Reset buffer
        clearCache();
        return TUBE_ERR_CMD_TOO_LONG;
    }
    memcpy(&cmd_buf[cmd_buf_len], new_cmd, len);
    cmd_buf_len += len;
    return TUBE_OK;
}

int lookForChar(char c) {
    int i;
    for (i = 0; i < cmd_buf_len; i++) {
        if (cmd_buf[i] == c) return i;
    }
    return -1;
}

void shiftBuf(int len) {
    // Don't do anything if the shift amount is too much
    if (len > cmd_buf_len) return;

    int i;
    for (i = 0; i < len; i++) {
        cmd_buf[i] = cmd_buf[i + len];
    }
    cmd_buf_len -= len;

}

int trimCrlf(void) {
    if (cmd_buf_len == 0) return 0;

    char c;
    int shift_len = 0;
    // Check first byte
    if (cmd_buf_len >= 1) {
         c = cmd_buf[0];
        if (c == '\r' || c == '\n') shift_len++;
    }
    // Check second byte
    if (cmd_buf_len >= 2) {
         c = cmd_buf[1];
        if (c == '\r' || c == '\n') shift_len++;
    }

    if (shift_len) shiftBuf(shift_len);

    return shift_len;
}

int crlfPos(void) {
    if (!cmd_buf_len) return 0;

    int cr_pos = lookForChar('\r');
    int lf_pos = lookForChar('\n');

    // Must be at least one of them for a complete command
    if (cr_pos == -1 && lf_pos == -1) return -1;

    // There is a \r but not an \n
    if (cr_pos != -1 && lf_pos == -1) return cr_pos;

    // There is an \n but not an \r
    if (cr_pos == 1 && lf_pos != -1) return lf_pos;

    // Return the first one
    return (cr_pos < lf_pos) ? cr_pos : lf_pos;
}

int commandSize(void) {
    int pos = crlfPos();
    return (pos != -1) ? pos : 0;
}

int noopCommand(void) {
    if (crlfPos() == -1) return 0;

    return trimCrlf();
}

int commandComplete(void) {
    return (crlfPos() != -1);
}

int cmdBufLen(void) {
    return cmd_buf_len;
}

int getCmd(char* buf, int buf_len) {
    if (crlfPos() == -1) return 0;

    int cmd_len = commandSize();
    if (cmd_len == 0) {
        // This was a noop command
        trimCrlf();
        return TUBE_ERR_CMD_NOOP;
    }

    // Buffer size safety check
    if (buf_len < cmd_len) {
        return TUBE_ERR_BUF_OVERRUN;
    }

    // Copy command
    memcpy(buf, cmd_buf, cmd_len);
    shiftBuf(cmd_len);
    trimCrlf();

    return 0;
}

int cmdParse(Command* cmd, char* buf, int len) {
    // Loop over cmd string and find each ':'
    int count = 0;
    int i;
    for (i = 0; i < len; i++) {
        if (buf[i] == ':') {
            if (count == CMD_MAX_NUM_ARGS) return TUBE_ERR_TOO_MANY_ARGS;
            buf[i] = '\0'; // Replace with null char
            // Pointer to argument
            cmd->args[count++] = &buf[++i];
        }
    }
    if (!count) return TUBE_ERR_WRONG_NUM_ARGS;

    buf[i] == '\0'; // Mark end of last arg
    cmd->buf = buf;
    cmd->numargs = count;

    // Get cmd type
    // Check print
    if (strcmp(tube_cmd_print, buf) == 0) {
        cmd->type = Print;
    }
    else {
        return TUBE_ERR_BAD_CMD;
    }

    return 0;
}

int cmdDecodePrint(char* buf, uint16_t* tube_bitmap, int bitmap_len) {
    CommandParseState state = Start;
    int buf_i = 0;
    int bit_i = 0;
    int token_i = 0;
    uint16_t space_bitmap = decodeChar(' ');
    char token_buf[CMD_MAX_TOKEN + 1];
    int buflen = strlen(buf);
    // Decode buffer
    while (buf[buf_i] != '\0' && bit_i < bitmap_len && buf_i < buflen) {
        char c = buf[buf_i];
        switch (state) {
        case Start:
            // First character must be printable or token start
            if (c == '{') {
                state = TokenStart;
                break;
            }

            if (!isPrintable(c)) return TUBE_ERR_PARSE;

            // Decode and set char
            tube_bitmap[bit_i++] = decodeChar(buf[buf_i]);
            state = Idle;
            break;
        case Idle:
            if (c == '{') {
                // Start token
                state = TokenStart;
            }
            else if (c == '!') {
                // Underline previous character
                tube_bitmap[bit_i - 1] =                 underlineCode(tube_bitmap[bit_i - 1]);
                state = Idle;
            }
            else {
                // Decode and set char
                tube_bitmap[bit_i++] = decodeChar(buf[buf_i]);
                state = Idle;
            }
            break;
        case TokenStart:
            if (c == '!') {
                // Underline all characters
                state = Underline;
                break;
            }
            token_i = 0;
        case Token:
            // Add to token buf
            if (c != '}') {
                if (token_i == CMD_MAX_TOKEN) {
                    // Run out of space in the buffer
                    return TUBE_ERR_PARSE;
                }
                token_buf[token_i++] = c;
                state = Token;
                break;
            }

            token_buf[token_i++] = '\0';
            int err = cmdDecodeToken(token_buf, &tube_bitmap[bit_i++]);
            if (err != TUBE_OK) {
                return err;
            }

            state = Idle;
        case Underline:
            // Underline all new characters
            if (c == '}') {
                state = Idle;
            }
            else {
                tube_bitmap[bit_i++] = decodeAndUnderline(c);
                state = Underline;
            }
            break;
        }
        buf_i++;
    }

    // Blank out remaining bitmaps
    while (bit_i < bitmap_len) {
        tube_bitmap[bit_i++] = space_bitmap;
    }

    return TUBE_OK;
}

int cmdDecodeToken(char* buf, uint16_t* bitmap) {
    if (strncmp("0x", buf, 2) == 0) {
        // Decode hex
        *bitmap = tokenDecodeHex(buf);
    }
    else if (strncmp("0X", buf, 2) == 0) {
        // Decode hex
        *bitmap = tokenDecodeHex(buf);
    }
    else if (strncmp("0b", buf, 2) == 0) {
        // Decode binary
        *bitmap = tokenDecodeBinary(buf);
    }
    else if (strncmp("0B", buf, 2) == 0) {
        // Decode binary
        *bitmap = tokenDecodeBinary(buf);
    }
    else {
        return TUBE_ERR_TOKEN;
    }

    return TUBE_OK;
}

uint16_t tokenDecodeHex(char* buf) {
    // Decode hex token
    int len = strlen(buf);
    return (uint16_t)(0xFFFF & strtol(buf, buf + len, 0));
}

uint16_t tokenDecodeBinary(char* buf) {
    int i;
    uint16_t bitmap = 0;
    for (i = 0; buf[i] != '\0'; i++) {
        char c = buf[i];
        if (buf != '0' || buf != '1') return NOCODE;

        bitmap = (bitmap << 1) | (c - '0');
    }
    return bitmap;
}

const char* tubeErrToText(int errcode) {
    switch (errcode) {
    case TUBE_OK:
        return "No error";
    case TUBE_ERROR_OTHER:
        return "Other tube error";
    case TUBE_ERR_BUF_OVERRUN:
        return "Buffer overrun";
    case TUBE_ERR_BAD_CMD:
        return "Unknown command";
    case TUBE_ERR_CMD_TOO_LONG:
        return "Command too long";
    case TUBE_ERR_CMD_NOOP:
        return "Noop command not handled";
    case CMD_MAX_NUM_ARGS:
        return "Too marny arguments";
    case TUBE_ERR_WRONG_NUM_ARGS:
        return "Wrong number of arguments";
    case TUBE_ERR_PARSE:
        return "Parse error";
    case TUBE_ERR_TOKEN:
        return "Token error";
    default:
        return "Unknown tube error";
    }
}

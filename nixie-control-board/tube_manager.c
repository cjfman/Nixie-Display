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
    int i;
    int space_rest = 0;
    uint16_t space_bitmap = decodeChar(' ');
    for (i = 0; i < bitmap_len; i++) {
        if (space_rest) {
            // Set space
            tube_bitmap[i] = space_bitmap;
        }
        else if (buf[i] == '\0') {
            // Reacehd end of buf
            space_rest = 1;
            tube_bitmap[i] = space_bitmap;
        }
        else {
            // Decode and set char
            tube_bitmap[i] = decodeChar(buf[i]);
        }
    }
    return TUBE_OK;
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
    default:
        return "Unknown tube error";
    }
}

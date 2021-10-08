#include <inttypes.h>
#include <stdlib.h>
#include <string.h>

#include "decoder.h"
#include "tube_manager.h"

// cmd prefixes
const char tube_cmd_print[] = "print:";

// Ring buffer
char cmd_buf[CMD_BUF_SIZE];
int cmd_buf_head = 0;
int cmd_buf_tail = 0;
int cmd_buf_len = 0;

int buildCmd(char* new_cmd, int len) {
    // Check size
    if (len + cmd_buf_len > CMD_BUF_SIZE) {
        // Buffer overrun
        // Reset buffer
        cmd_buf_head = 0;
        cmd_buf_tail = 0;
        cmd_buf_len = 0;
        return TUBE_ERR_CMD_TOO_LONG;
    }

    // Copy command section into ring buffer
    if (cmd_buf_tail + len <= CMD_BUF_SIZE) {
        // Segment fits in remainder of buffer
        memcpy(&cmd_buf[cmd_buf_tail], new_cmd, len);
        cmd_buf_tail += len;
    }
    else {
        // New command section wraps around buffer
        int to_end = CMD_BUF_SIZE - cmd_buf_tail;
        memcpy(&cmd_buf[cmd_buf_tail], new_cmd, to_end);
        memcpy(cmd_buf, &new_cmd[to_end], len - to_end);
        cmd_buf_tail = len - to_end;
    }
    cmd_buf_len += len;
    return TUBE_OK;
}

int lookForChar(char c) {
    int i;
    if (cmd_buf_head < cmd_buf_tail) {
        // Potential command is continuous
        for (i = cmd_buf_head; i < cmd_buf_tail; i++) {
            if (cmd_buf[i] == c) return i;
        }
    }
    else {
        // Potential command wraps around buffer
        // Check to end of buffer
        for (i = cmd_buf_head; i < CMD_BUF_SIZE; i++) {
            if (cmd_buf[i] == c) return i;
        }
        // Check from start of buffer
        for (i = 0; i < cmd_buf_tail; i++) {
            if (cmd_buf[i] == c) return i;
        }
    }
    return -1;
}

int lenToChar(char c) {
    int i;
    if (cmd_buf_head < cmd_buf_tail) {
        // Potential command is continuous
        for (i = cmd_buf_head; i < cmd_buf_tail; i++) {
            if (cmd_buf[i] == c) return i - cmd_buf_head;
        }
    }
    else {
        // Potential command wraps around buffer
        // Check to end of buffer
        for (i = cmd_buf_head; i < CMD_BUF_SIZE; i++) {
            if (cmd_buf[i] == c) return i - cmd_buf_head;
        }
        // Check from start of buffer
        for (i = 0; i < cmd_buf_tail; i++) {
            if (cmd_buf[i] == c) return CMD_BUF_SIZE - cmd_buf_head + i;
        }
    }

    return -1;
}

int trimCrlf(void) {
    if (cmd_buf_len == 0) return 0;

    // Look for crlf
    char c = cmd_buf[cmd_buf_head];
    if (c == '\r' || c == '\n') {
        cmd_buf_head = (cmd_buf_head + 1) % CMD_BUF_SIZE;
        cmd_buf_len--;
    }

    // Do it again
    if (cmd_buf_len == 0) return 1;

    // Look for crlf
    c = cmd_buf[cmd_buf_head];
    if (c == '\r' || c == '\n') {
        cmd_buf_head = (cmd_buf_head + 1) % CMD_BUF_SIZE;
        cmd_buf_len--;
    }

    return 2;
}

int lenToCrlf(void) {
    if (!cmd_buf_len) return 0;

    int cr_pos = lookForChar('\r');
    int lf_pos = lookForChar('\n');

    // Must be at least one of them for a complete command
    if (cr_pos == -1 && lf_pos == -1) return -1;

    // There is a \r but not an \n
    if (cr_pos != -1 && lf_pos == -1) return lenToChar('\r');

    // There is an \n but not an \r
    if (cr_pos == 1 && lf_pos != -1) return lenToChar('\n');

    // Return the first one
    int to_cr = lenToChar('\r');
    int to_lf = lenToChar('\n');
    return (to_cr < to_lf) ? to_cr : to_lf;
}

int commandSize(void) {
    int pos = lenToCrlf();
    return (pos != -1) ? pos - 1 : 0;
}

int noopCommand(void) {
    if (lenToCrlf() == -1) return 0;

    return trimCrlf();
}

int cmdBufLen(void) {
    return cmd_buf_len;
}

int getCmd(char* buf, int buf_len) {
    int cmd_len = commandSize();
    if (!cmd_len) {
        return 0;
    }

    // Buffer size safety check
    if (buf_len < cmd_len) {
        return TUBE_ERR_BUF_OVERRUN;
    }

    int i;
    if (cmd_buf_head < cmd_buf_tail) {
        // Command is continuous
        memcpy(buf, &cmd_buf[cmd_buf_head], cmd_len);
        cmd_buf_head += cmd_len;
    }
    else {
        // Command wraps around buffer
        int to_end = CMD_BUF_SIZE - cmd_buf_len;
        memcpy(buf, &cmd_buf[cmd_buf_head], to_end);
        int remaining = cmd_len - to_end;
        memcpy(&buf[to_end], cmd_buf, remaining);
        cmd_buf_head = remaining;
    }

    cmd_buf_len -= cmd_len;
    trimCrlf();

    return cmd_len;
}

int cmdType(char* buf) {
    // There is only one command right now
    // Check "print"
    if (memcmp(tube_cmd_print, buf, strlen(tube_cmd_print)) == 0) {
        return TUBE_CMD_PRINT;
    }

    return TUBE_ERR_BAD_CMD;
}

int cmdArgStart(char* buf, int len) {
    int i;
    for (i = 0; i < len; i++) {
        if (buf[i] == ':') return i + 1;
    }
    return TUBE_ERR_BAD_CMD;
}

int cmdDecodePrint(char* buf, int buf_len, uint16_t* tube_bitmap, int bitmap_len) {
    int i;
    uint16_t space_bitmap = decodeChar(' ');
    for (i = 0; i < bitmap_len; i++) {
        // If command shorter than tube array, fill rest with spaces
        tube_bitmap[i] = (i < buf_len) ? buf[i] : space_bitmap;
    }
    return TUBE_OK;
}

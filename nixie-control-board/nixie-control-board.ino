#include <SPI.h>

#include "decoder.h"
#include "tube_manager.h"

#define STROBE_PIN 2
#define OE_PIN 4


void newline() {
    Serial.print("\n");
}

void debugPrint(String msg) {
    newline();
    Serial.print(msg);
    newline();
}

void printPrompt() {
    Serial.print("> ");
}

bool printable(char c) {
    // Space starts at 0x20
    return (c >= 0x20 && c < 0x7f);
}

String intToString(int i) {
    char s[10];
    snprintf(s, 10, "%d", i);
    return String(s);
}

void mirror() {
    if (Serial.available() == 0) return;
    char c = Serial.read();
    Serial.write(c);
    if (printable(c)) {
        Serial.write(c);
    }
    else if (c == '\n') {
        newline();
    }
}

void enableTube() {
    digitalWrite(OE_PIN, HIGH);
}

void disableTube() {
    digitalWrite(OE_PIN, LOW);
}


void setTube(uint16_t val) {
    disableTube();
    SPI.beginTransaction(SPISettings(100000, MSBFIRST, SPI_MODE2));
    SPI.transfer(val >> 8);
    SPI.transfer(val & 0xFF);
    // Do it twice for the LEDs
    SPI.transfer(val >> 8);
    SPI.transfer(val & 0xFF);
    SPI.endTransaction();
    enableTube();
}

void clearTube() {
    setTube(0x00);
}

void setTubes(uint16_t* tube_bitmaps, int num_tubes) {
    disableTube();
    SPI.beginTransaction(SPISettings(100000, MSBFIRST, SPI_MODE2));

    // Shift bitmaps out in reverse order
    int i;
    for (i = 0; i < num_tubes; i++) {
        uint16_t bitmap = tube_bitmaps[num_tubes - i - 1];
        SPI.transfer(bitmap >> 8);
        SPI.transfer(bitmap & 0xFF);
    }
    SPI.endTransaction();
    enableTube();
}

void setup() {
    Serial.begin(9600);
    pinMode(STROBE_PIN, OUTPUT);
    disableTube();
    pinMode(OE_PIN, OUTPUT);
    SPI.begin();
    clearTube();
    digitalWrite(STROBE_PIN, LOW);
    delay(1000);
    digitalWrite(STROBE_PIN, HIGH);

    Serial.print("Nixie tube command terminal\n");
    //Serial.print("Test spin!\n");
    //spin();
    printPrompt();
}

void mirrorLoop() {
    if (Serial.available() == 0) return;

    mirror();
}

void assignLoop() {
    if (Serial.available() == 0) return;

    char c = Serial.read();
    if (c == '\n') return;

    setTube(decodeChar(c));
}

void spin() {
    uint16_t val = 1;
    clearTube();
    delay(125);
    for (int i = 0; i < 16; i++) {
        setTube(val);
        val <<= 1;
        delay(125);
    }
    clearTube();
}

void testLoop() {
    spin();
    delay(1000);
}

void printError(int errcode) {
    newline();
    switch (errcode) {
    case TUBE_ERR_CMD_TOO_LONG:
        Serial.print("NAK: Command too long\n");
        break;
    case TUBE_ERR_BAD_CMD:
        Serial.print("NAK: Unknown command\n");
    default:
        Serial.print("NAK: Unknown error\n");
    }
}

void tubeManagerLoop(void) {
    char cmd_buf[CMD_BUF_SIZE];

    int read_len = Serial.available();
    if (read_len == 0) return;

    // Read bytes
    int total = Serial.readBytes(cmd_buf, read_len);
    if (total != read_len) {
        Serial.print("NAK: Unknown error\n");
        newline();
        printPrompt();
        return;
    }
    String num = intToString(read_len);

    // Build command
    int errcode = buildCmd(cmd_buf, read_len);
    if (errcode) {
        printError(errcode);
        printPrompt();
        return;
    }

    Serial.print(String("\nBuf len: ") + intToString(cmdBufLen()) + "\n");

    // Check for noop command
    if (noopCommand()) {
        Serial.print("\nNoop\n");
        printPrompt();
        return;
    }

    // Check for full command
    if (!commandSize()) {
        return;
    }
    errcode = getCmd(cmd_buf, CMD_BUF_SIZE);
    if (errcode) {
        printError(errcode);
        printPrompt();
        return;
    }
    char* cmd_args = cmd_buf + cmdArgStart(cmd_buf, CMD_BUF_SIZE);
    uint16_t tube_bitmaps[NUM_TUBES];
    cmdDecodePrint(cmd_args, cmd_args - cmd_buf, tube_bitmaps, NUM_TUBES);
    setTubes(tube_bitmaps, NUM_TUBES);
    Serial.print("> ");
    return;
}

void loop() {
    tubeManagerLoop();
    delay(1);
}

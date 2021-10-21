#include <SPI.h>

extern "C" {
    #include "decoder.h"
    #include "tube_manager.h"
}


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
    int i;
    for (i = 0; i < 4; i++) {
        SPI.transfer(val >> 8);
        SPI.transfer(val & 0xFF);
    }
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
    for (int i = 0; i < NUM_TUBES; i++) {
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


void fillLoop() {
    int i;
    int x = 0;
    for (i = 0; i < 16; i++) {
        setTube(x);
        x <<= 1;
        x |= 0x01;
        delay(125);
    }
    delay(1000);
    clearTube();
}

void printErrorCode(int errcode) {
    newline();
    Serial.print("NAK: ");
    Serial.print(tubeErrToText(errcode));
    Serial.write('\n');
}

void printError(char* msg) {
    Serial.print("NAK: ");
    Serial.print(msg);
    Serial.write('\n');
}

void tubeManagerLoop(void) {
    char cmd_buf[CMD_BUF_SIZE];
    memset(cmd_buf, '\0', CMD_BUF_SIZE);

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
        printErrorCode(errcode);
        printPrompt();
        return;
    }

    // Check for full command
    if (!commandComplete()) {
        return;
    }

    // Check for noop command
    if (noopCommand()) {
        Serial.print("\nNoop\n");
        printPrompt();
        return;
    }

    // Load command
    errcode = getCmd(cmd_buf, CMD_BUF_SIZE);
    if (errcode) {
        printErrorCode(errcode);
        printPrompt();
        return;
    }

    // Parse command
    Command cmd;
    errcode = cmdParse(&cmd, cmd_buf, CMD_BUF_SIZE);
    if (errcode) {
        printErrorCode(errcode);
        printPrompt();
        return;
    }
    if (cmd.type != Print) {
       printError("Unsupported command\n");
       printPrompt();
       return;
    }
    Serial.print("Print: '");
    Serial.print(cmd.args[0]);
    Serial.print("'\n");
    uint16_t tube_bitmaps[NUM_TUBES];
    cmdDecodePrint(cmd.args[0], tube_bitmaps, NUM_TUBES);
    setTubes(tube_bitmaps, NUM_TUBES);
    printPrompt();
    return;
}

void loop() {
    //assignLoop();
    //tubeManagerLoop();
    //testLoop();
    fillLoop();
    delay(1);
}

#include <SPI.h>

#include "decoder.h"

#define STROBE_PIN 2
#define OE_PIN 4

void newline() {
    Serial.print("\n\r");
}

bool printable(char c) {
    // Space starts at 0x20
    return (c >= 0x20 && c < 0x7f);
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

void setup() {
    Serial.begin(9600);
    Serial.print("Hello, World!\n\n\r");
    pinMode(STROBE_PIN, OUTPUT);
    disableTube();
    pinMode(OE_PIN, OUTPUT);
    SPI.begin();
    clearTube();
    digitalWrite(STROBE_PIN, LOW);
    delay(1000);
    digitalWrite(STROBE_PIN, HIGH);
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

void testLoop() {
    uint16_t val = 1;
    clearTube();
    delay(500);
    for (int i = 0; i < 16; i++) {
        setTube(val);
        val <<= 1;
        delay(500);
    }
    clearTube();
    delay(1000);
}


void loop() {
    assignLoop();
    delay(1);
}

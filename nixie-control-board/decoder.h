#ifndef DECODER_H
#define DECODER_H

#define NOCODE 0x3FFF

uint16_t decodeChar(char c);
int isPrintable(char c);
uint16_t underlineCode(uint16_t code);
uint16_t decodeAndUnderline(char c);

#endif // DECODER_H

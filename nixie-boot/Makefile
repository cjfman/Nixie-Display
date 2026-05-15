CC     = gcc
CFLAGS = -Wall -O2 -I../nixie-control-board
TARGET = nixie_boot
SRCS   = nixie_boot.c ../nixie-control-board/decoder.c

$(TARGET): $(SRCS) ../nixie-control-board/decoder.h
	$(CC) $(CFLAGS) -o $(TARGET) $(SRCS)

clean:
	rm -f $(TARGET)

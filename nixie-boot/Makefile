CC     = gcc
CFLAGS = -Wall -O2 -I../nixie-control-board
TARGET = nixie_boot
SRCS   = nixie_boot.c ../nixie-control-board/decoder.c

CROSS_IMAGE      = nixie-cross
CROSS_CC         = arm-linux-gnueabihf-gcc
# Pi Zero W is ARMv6; standard Debian armhf defaults to ARMv7
CROSS_ARCH_FLAGS = -march=armv6 -marm -mfpu=vfp -mfloat-abi=hard

$(TARGET): $(SRCS) ../nixie-control-board/decoder.h
	$(CC) $(CFLAGS) -o $(TARGET) $(SRCS)

cross:
	docker build -q -f Dockerfile.cross -t $(CROSS_IMAGE) .
	docker run --rm \
	    -v "$(abspath ..):/src" \
	    -w /src/nixie-boot \
	    $(CROSS_IMAGE) \
	    $(CROSS_CC) $(CFLAGS) $(CROSS_ARCH_FLAGS) -o $(TARGET) $(SRCS)

clean:
	rm -f $(TARGET)

.PHONY: cross clean

/*
 * nixie_boot.c - Send a short text message to the nixie display over SPI.
 *
 * Intended as a fast boot-time display initializer that avoids Python
 * startup overhead. Reuses decoder.c from the nixie-control-board firmware.
 *
 * GPIO pin numbers are BCM (not physical/BOARD):
 *   OE_PIN     5  (physical 29) - output enable, active HIGH
 *   HV_PIN    27  (physical 13) - high voltage enable, active HIGH
 *   STROBE_PIN 22  (physical 15) - shift register strobe, kept HIGH (inactive)
 *
 * Usage: nixie_boot [message]
 *   Defaults to "Booting..." if no argument is given.
 */

#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <linux/spi/spidev.h>

#include "../nixie-control-board/decoder.h"

#define NUM_TUBES    16
#define SPI_DEVICE   "/dev/spidev0.0"
#define SPI_SPEED_HZ 1000000

#define OE_PIN     5
#define HV_PIN     27
#define STROBE_PIN 22

#define GPIO_ROOT "/sys/class/gpio"

/* ---- GPIO via sysfs ---------------------------------------------------- */

static void gpio_write(const char *path, const char *val) {
    int fd = open(path, O_WRONLY);
    if (fd < 0) { perror(path); return; }
    write(fd, val, strlen(val));
    close(fd);
}

static void gpio_export(int pin) {
    char path[64], val[8];
    snprintf(path, sizeof(path), GPIO_ROOT "/gpio%d", pin);
    if (access(path, F_OK) == 0) return;  /* already exported */
    snprintf(val,  sizeof(val),  "%d", pin);
    gpio_write(GPIO_ROOT "/export", val);
}

static void gpio_direction(int pin, const char *dir) {
    char path[64];
    snprintf(path, sizeof(path), GPIO_ROOT "/gpio%d/direction", pin);
    gpio_write(path, dir);
}

static void gpio_set(int pin, int value) {
    char path[64];
    snprintf(path, sizeof(path), GPIO_ROOT "/gpio%d/value", pin);
    gpio_write(path, value ? "1" : "0");
}

static void setup_gpio(void) {
    int pins[] = {OE_PIN, HV_PIN, STROBE_PIN};
    for (int i = 0; i < 3; i++) {
        gpio_export(pins[i]);
        gpio_direction(pins[i], "out");
    }
}

/* ---- SPI --------------------------------------------------------------- */

static int spi_send(uint8_t *data, int len) {
    int fd = open(SPI_DEVICE, O_RDWR);
    if (fd < 0) { perror("open " SPI_DEVICE); return -1; }

    uint8_t  mode  = SPI_MODE_2;
    uint32_t speed = SPI_SPEED_HZ;
    ioctl(fd, SPI_IOC_WR_MODE,         &mode);
    ioctl(fd, SPI_IOC_WR_MAX_SPEED_HZ, &speed);

    struct spi_ioc_transfer xfer = {
        .tx_buf        = (unsigned long)data,
        .len           = (uint32_t)len,
        .speed_hz      = SPI_SPEED_HZ,
        .bits_per_word = 8,
    };

    int ret = ioctl(fd, SPI_IOC_MESSAGE(1), &xfer);
    close(fd);
    return (ret < 0) ? -1 : 0;
}

/* ---- Main -------------------------------------------------------------- */

int main(int argc, char *argv[]) {
    const char *msg = (argc > 1) ? argv[1] : "Booting...";

    /* Encode each character to a 14-segment bitmap */
    uint16_t bitmaps[NUM_TUBES] = {0};
    int len = (int)strlen(msg);
    for (int i = 0; i < NUM_TUBES && i < len; i++) {
        uint16_t bm = decodeChar(msg[i]);
        bitmaps[i]  = (bm == NOCODE) ? 0 : bm;
    }

    /* Pack into SPI bytes: tube 15 first, each tube as MSB then LSB */
    uint8_t data[NUM_TUBES * 2];
    for (int i = 0; i < NUM_TUBES; i++) {
        uint16_t bm    = bitmaps[NUM_TUBES - 1 - i];
        data[i * 2]    = (bm >> 8) & 0xFF;
        data[i * 2 + 1] = bm & 0xFF;
    }

    setup_gpio();
    gpio_set(HV_PIN,     1);  /* enable high voltage */
    gpio_set(STROBE_PIN, 1);  /* strobe inactive */
    gpio_set(OE_PIN,     0);  /* disable output while loading data */

    if (spi_send(data, sizeof(data)) < 0) {
        fprintf(stderr, "SPI send failed\n");
        return 1;
    }

    gpio_set(OE_PIN, 1);      /* enable output, tubes now show message */
    return 0;
}

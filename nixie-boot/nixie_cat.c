/*
 * nixie_cat.c - Send text to the nixie display over SPI.
 *
 * Intended as a fast boot-time display initializer that avoids Python
 * startup overhead. Reuses decoder.c from the nixie-control-board firmware.
 *
 * GPIO pin numbers are BCM (not physical/BOARD):
 *   OE_PIN     5  (physical 29) - output enable, active HIGH
 *   HV_PIN    27  (physical 13) - high voltage enable, active HIGH
 *   STROBE_PIN 22  (physical 15) - shift register strobe, kept HIGH (inactive)
 *
 * Usage:
 *   nixie_boot word1 word2 ...   display args joined by spaces (like echo)
 *   echo "text" | nixie_boot     stream stdin to display as chars arrive
 *   nixie_boot                   clear the display
 */

#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
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

static int gpio_write(const char *path, const char *val) {
    int fd = open(path, O_WRONLY);
    if (fd < 0) { perror(path); return -1; }
    int n = strlen(val);
    int ret = (write(fd, val, n) == n) ? 0 : -1;
    if (ret < 0) perror(path);
    close(fd);
    return ret;
}

static int gpio_export(int pin) {
    char path[64], val[8];
    snprintf(path, sizeof(path), GPIO_ROOT "/gpio%d", pin);
    if (access(path, F_OK) == 0) return 0;
    snprintf(val, sizeof(val), "%d", pin);
    return gpio_write(GPIO_ROOT "/export", val);
}

static int gpio_direction(int pin, const char *dir) {
    char path[64];
    snprintf(path, sizeof(path), GPIO_ROOT "/gpio%d/direction", pin);
    return gpio_write(path, dir);
}

static int gpio_set(int pin, int value) {
    char path[64];
    snprintf(path, sizeof(path), GPIO_ROOT "/gpio%d/value", pin);
    return gpio_write(path, value ? "1" : "0");
}

static int setup_gpio(void) {
    int pins[] = {OE_PIN, HV_PIN, STROBE_PIN};
    for (int i = 0; i < 3; i++) {
        if (gpio_export(pins[i])           < 0) return -1;
        if (gpio_direction(pins[i], "out") < 0) return -1;
    }
    if (gpio_set(HV_PIN,     1) < 0) return -1;
    if (gpio_set(STROBE_PIN, 1) < 0) return -1;
    return 0;
}

/* ---- SPI --------------------------------------------------------------- */

static int spi_send_raw(uint8_t *data, int len) {
    int fd = open(SPI_DEVICE, O_RDWR);
    if (fd < 0) { perror("open " SPI_DEVICE); return -1; }

    uint8_t  mode  = SPI_MODE_2;
    uint32_t speed = SPI_SPEED_HZ;
    if (ioctl(fd, SPI_IOC_WR_MODE,         &mode)  < 0) { perror("SPI_IOC_WR_MODE");         close(fd); return -1; }
    if (ioctl(fd, SPI_IOC_WR_MAX_SPEED_HZ, &speed) < 0) { perror("SPI_IOC_WR_MAX_SPEED_HZ"); close(fd); return -1; }

    struct spi_ioc_transfer xfer = {
        .tx_buf        = (unsigned long)data,
        .len           = (uint32_t)len,
        .speed_hz      = SPI_SPEED_HZ,
        .bits_per_word = 8,
    };

    int ret = ioctl(fd, SPI_IOC_MESSAGE(1), &xfer);
    if (ret < 0) perror("SPI_IOC_MESSAGE");
    close(fd);
    return (ret < 0) ? -1 : 0;
}

/* ---- Display ----------------------------------------------------------- */

static int display_string(const char *s, int len) {
    uint16_t bitmaps[NUM_TUBES] = {0};
    int start = (len > NUM_TUBES) ? len - NUM_TUBES : 0;
    for (int i = start, j = 0; i < len && j < NUM_TUBES; i++, j++) {
        uint16_t bm = decodeChar(s[i]);
        bitmaps[j] = (bm == NOCODE) ? 0 : bm;
    }

    uint8_t data[NUM_TUBES * 2];
    for (int i = 0; i < NUM_TUBES; i++) {
        uint16_t bm    = bitmaps[NUM_TUBES - 1 - i];
        data[i * 2]    = (bm >> 8) & 0xFF;
        data[i * 2 + 1] = bm & 0xFF;
    }

    gpio_set(OE_PIN, 0);
    int ret = spi_send_raw(data, sizeof(data));
    gpio_set(OE_PIN, 1);
    return ret;
}

/* ---- Input modes ------------------------------------------------------- */

static int display_from_args(int argc, char *argv[]) {
    int total = 0;
    for (int i = 1; i < argc; i++) {
        total += strlen(argv[i]);
        if (i < argc - 1) total++;  /* space between args */
    }

    char *buf = malloc(total + 1);
    if (!buf) { perror("malloc"); return -1; }
    buf[0] = '\0';
    for (int i = 1; i < argc; i++) {
        strcat(buf, argv[i]);
        if (i < argc - 1) strcat(buf, " ");
    }

    int ret = display_string(buf, total);
    free(buf);
    return ret;
}

static int display_from_stdin(void) {
    char buf[4096];
    int len = 0;
    int pending_newline = 0;
    int got_any = 0;
    char c;
    int n;

    while ((n = read(STDIN_FILENO, &c, 1)) == 1) {
        if (c == '\n' || c == '\r') {
            pending_newline = 1;
            continue;
        }
        if (pending_newline) {
            len = 0;
            pending_newline = 0;
        }
        if (len < (int)sizeof(buf) - 1)
            buf[len++] = c;
        buf[len] = '\0';
        if (display_string(buf, len) < 0)
            return -1;
        got_any = 1;
    }
    if (n < 0) { perror("read"); return -1; }

    if (!got_any)
        return display_string("", 0);
    return 0;
}

/* ---- Main -------------------------------------------------------------- */

int main(int argc, char *argv[]) {
    if (setup_gpio() < 0)
        return 1;

    int ret;
    if (argc > 1)
        ret = display_from_args(argc, argv);
    else if (!isatty(STDIN_FILENO))
        ret = display_from_stdin();
    else
        ret = display_string("", 0);

    return (ret < 0) ? 1 : 0;
}

/* Six-face pose consumer: real GPIO input and BMI088 SPI bursts. */
#include "board.h"
#define SPI_CR1 REG32(0x40003800u)
#define SPI_SR REG32(0x40003814u)
#define SPI_TXDR REG32(0x40003820u)
#define SPI_RXDR REG32(0x40003830u)
#include "dm_mc02_bmi088_spi_fixture.h"

volatile uint32_t ticks_ms;
void SysTick_Handler(void) { ++ticks_ms; }

static void hex_byte(uint8_t value)
{
    static const char hex[] = "0123456789abcdef";
    char text[] = {hex[value >> 4], hex[value & 15], 0};
    uart_text(text);
}

void firmware_main(void)
{
    board_init();
    GPIOC_BSRR = 9u;
    GPIOC_MODER = (GPIOC_MODER & ~((3u << 0) | (3u << 6))) |
                  (1u << 0) | (1u << 6);
    SPI_CR1 = 1u;
    bmi_write_byte(1, 0x7c, 0x00);
    bmi_write_byte(1, 0x7d, 0x04);
    bmi_write_byte(8, 0x11, 0x00);
    bmi_write_byte(8, 0x0f, 0x01);
    bmi_write_byte(8, 0x10, 0x05);
    bmi_write_byte(1, 0x40, 0x88);
    bmi_write_byte(1, 0x41, 0x01);
    while (ticks_ms < 30) { idle(); }
    uart_text("POSE_READY\n");
    uint32_t previous = 1;
    for (;;) {
        uint32_t key = (GPIOA_IDR >> 15) & 1u;
        if (previous && !key) {
            uint8_t bytes[6];
            bmi_read_bytes(8, 0x02, 0, bytes, 6);
            uart_text("POSE g=");
            for (unsigned i = 0; i < 6; ++i) { hex_byte(bytes[i]); }
            bmi_read_bytes(1, 0x12, 1, bytes, 6);
            uart_text(" a=");
            for (unsigned i = 0; i < 6; ++i) { hex_byte(bytes[i]); }
            uart_text("\n");
        }
        previous = key;
        if (ticks_ms > 5000) {
            uart_text("POSE_TIMEOUT\n");
            for (;;) { idle(); }
        }
        idle();
    }
}

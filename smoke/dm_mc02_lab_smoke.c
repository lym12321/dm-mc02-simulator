/* Test-only direct consumer for the Virtual Lab channel adapter.
 * Register configuration reuses dm_mc02_uart_smoke.c, dm_mc02_fdcan_smoke.c
 * and dm_mc02_adc_irq_smoke.c. Addresses/fields are STM32H723 CMSIS / RM0468
 * USART, GPIO, ADC and FDCAN definitions. This is a digital simulator fixture,
 * not a physical calibration or a complete hardware initialization sequence.
 */
#include <stdint.h>

#define REG32(address) (*(volatile uint32_t *)(address))
#define USART1_CR1  REG32(0x40011000u)
#define USART1_BRR  REG32(0x4001100cu)
#define USART1_ISR  REG32(0x4001101cu)
#define USART1_RDR  REG32(0x40011024u)
#define USART1_TDR  REG32(0x40011028u)
#define GPIOA_MODER REG32(0x58020000u)
#define GPIOA_IDR   REG32(0x58020010u)
#define GPIOA_BSRR  REG32(0x58020018u)
#define ADC1_ISR    REG32(0x40022000u)
#define ADC1_CR     REG32(0x40022008u)
#define ADC1_CFGR   REG32(0x4002200cu)
#define ADC1_SMPR1  REG32(0x40022014u)
#define ADC1_SQR1   REG32(0x40022030u)
#define ADC1_DR     REG32(0x40022040u)
#define FDCAN1_CCCR REG32(0x4000a018u)
#define FDCAN1_GFC  REG32(0x4000a080u)
#define FDCAN1_SIDFC REG32(0x4000a084u)
#define FDCAN1_RXF0C REG32(0x4000a0a0u)
#define FDCAN1_RXF0S REG32(0x4000a0a4u)
#define FDCAN1_RXF0A REG32(0x4000a0a8u)
#define FDCAN1_RXESC REG32(0x4000a0bcu)
#define FDCAN1_TXBC REG32(0x4000a0c0u)
#define FDCAN1_TXESC REG32(0x4000a0c8u)
#define FDCAN1_TXBAR REG32(0x4000a0d0u)
#define MSGRAM ((volatile uint32_t *)0x4000ac00u)

void Reset_Handler(void);

__attribute__((used, section(".isr_vector")))
const uint32_t vector_table[] = {
    0x20020000u,
    (uint32_t)(uintptr_t)Reset_Handler,
};

static void uart_char(uint8_t value)
{
    /* TC means the preceding byte actually left the USART, rather than only
     * being admitted to the model's bounded host queue. */
    while ((USART1_ISR & (1u << 6)) == 0) {
    }
    USART1_TDR = value;
}

static void uart_text(const char *text)
{
    while (*text) {
        uart_char((uint8_t)*text++);
    }
}

static void uart_decimal(uint32_t value)
{
    char digits[10];
    unsigned count = 0;
    do {
        digits[count++] = '0' + value % 10u;
        value /= 10u;
    } while (value);
    while (count) {
        uart_char((uint8_t)digits[--count]);
    }
    uart_char('\n');
}

/* SPI1 MOSI PA7, 4 MHz: five SPI bits encode one WS2812 GRB bit.
 * 11000 gives H500/L750ns, 11100 gives H750/L500ns. The zero suffix
 * supplies the 50us reset interval. DMA1 stream3 reads real AXI SRAM. */
static void led_color(uint32_t rgb)
{
    volatile uint8_t *bytes = (volatile uint8_t *)0x24001000u;
    uint32_t grb = ((rgb & 0xff00u) << 8) | ((rgb >> 8) & 0xff00u) | (rgb & 0xffu);
    for (unsigned i = 0; i < 40; ++i) { bytes[i] = 0; }
    for (unsigned i = 0; i < 24; ++i) {
        unsigned symbol = (grb & (1u << (23 - i))) ? 28u : 24u;
        for (unsigned j = 0; j < 5; ++j) {
            unsigned bit = i * 5 + j;
            bytes[bit / 8] |= ((symbol >> (4 - j)) & 1u) << (7 - bit % 8);
        }
    }
    REG32(0x40013000u) = 0; /* Disable between transactions. */
    REG32(0x40013018u) = 0xffffffffu;
    REG32(0x40013004u) = 40;
    REG32(0x40020058u) = 0;
    REG32(0x40020008u) = 0x0f400000u; /* Clear stream3 flags. */
    REG32(0x40020060u) = 0x40013020u;
    REG32(0x40020064u) = 0x24001000u;
    REG32(0x4002005cu) = 40;
    REG32(0x4002080cu) = 38; /* SPI1_TX */
    REG32(0x40020058u) = 1u | (1u << 6) | (1u << 10);
    REG32(0x40013000u) = 1u | (1u << 9);
    while (!(REG32(0x40013014u) & 8u)) { }
}

void Reset_Handler(void)
{
    uint32_t last_key = 2u;
    uint32_t last_adc = 0x10000u;
    volatile uint32_t *tx = MSGRAM;
    volatile uint32_t *rx = MSGRAM + 32;

    USART1_BRR = 556u; /* Nominal 64 MHz kernel / 115200 baud, digital 8N1. */
    USART1_CR1 = 1u | (1u << 2) | (1u << 3); /* UE, RE, TE. */
    GPIOA_MODER = (GPIOA_MODER & ~((3u << 14) | (3u << 30))) |
                  (2u << 14); /* A7 SPI1, A15 USER input. */
    REG32(0x5802000cu) = (2u << 14) | (1u << 30); /* DIN pull-down, USER pull-up */
    REG32(0x58020020u) = 5u << 28;
    REG32(0x580244d8u) |= 1u; /* DMA1EN */
    REG32(0x580244f0u) |= 1u << 12; /* SPI1EN */
    REG32(0x58024450u) = (REG32(0x58024450u) & ~(7u << 12)) | (4u << 12);
    REG32(0x40013008u) = 7u | (3u << 28) | (1u << 15); /* 8bit, /16, TXDMAEN */
    REG32(0x4001300cu) = (1u << 22) | (1u << 17) | (1u << 26);
    REG32(0x58020400u) = (REG32(0x58020400u) & ~(3u << 30)) | (2u << 30);
    REG32(0x58020424u) = (REG32(0x58020424u) & ~(15u << 28)) | (2u << 28);
    REG32(0x40001828u) = 63; /* TIM12: 64MHz /64 /500 = 2kHz */
    REG32(0x4000182cu) = 499;
    REG32(0x40001838u) = 250;
    REG32(0x40001818u) = 6u << 12;
    REG32(0x40001820u) = 1u << 4;
    REG32(0x40001814u) = 1;

    FDCAN1_CCCR = 3u; /* INIT + configuration-change enable. */
    FDCAN1_TXBC = 1u << 24; /* TFQS=1, no dedicated buffers, FIFO at byte 0. */
    FDCAN1_TXESC = 0;
    FDCAN1_RXF0C = 128u | (1u << 16); /* One 8-byte RX element at byte 128. */
    FDCAN1_RXESC = 0;
    FDCAN1_SIDFC = 400u | (1u << 16); /* One standard filter at byte 400. */
    MSGRAM[100] = (2u << 30) | (1u << 27) | (0x321u << 16) | 0x7ffu;
    FDCAN1_GFC = (2u << 4) | (2u << 2) | 3u; /* Reject unmatched and RTR. */
    FDCAN1_CCCR = 0;

    ADC1_SQR1 = 4u << 6; /* One regular rank, channel 4. */
    ADC1_SMPR1 = 7u << 12; /* Long sample period reduces fixture polling load. */
    ADC1_CFGR = (1u << 13) | (1u << 12); /* 16-bit, continuous, overwrite OVR. */
    ADC1_CR = 1u;
    ADC1_CR = 1u | (1u << 2);
    uart_text("LAB_READY\n");

    for (;;) {
        uint32_t key = (GPIOA_IDR >> 15) & 1u;
        if (key != last_key) {
            led_color(key ? 0 : 0xffffffu);
            REG32(0x40001800u) = key ? 0 : 1;
            uart_text("KEY=");
            uart_decimal(key);
            last_key = key;
        }
        if (USART1_ISR & (1u << 5)) {
            uint8_t value = USART1_RDR;
            if (value == '^') {
                led_color(0x010408u);
                REG32(0x40001800u) = 1;
                uart_text("LED=010408\n");
            } else if (value == '~') {
                /* Terminal presentation fixture: erase, home, SGR, cursor,
                 * carriage return, backspace and erase-to-end-of-line. */
                uart_text("\033[2J\033[H\033[31mRED\033[0m\n"
                          "abc\bX\rZ\033[K\033[3;4H"
                          "\033[38;2;10;20;30mRGB\033[0m");
            } else {
                uart_text("UART=");
                uart_char(value);
                uart_char('\n');
            }
        }
        if (ADC1_ISR & (1u << 2)) {
            uint32_t value = ADC1_DR;
            ADC1_ISR = (1u << 3) | (1u << 4); /* W1C EOS/OVR after DR read. */
            if (value != last_adc) {
                uart_text("ADC=");
                uart_decimal(value);
                last_adc = value;
            }
        }
        if (FDCAN1_RXF0S & 0x7fu) {
            /* Reject unexpected native headers rather than echoing any input.
             * Timestamp/filter metadata in RX word 1 is independent of DLC. */
            if (rx[0] == (0x321u << 18) &&
                (rx[1] & 0x003f0000u) == (8u << 16)) {
                tx[0] = 0x322u << 18;
                tx[1] = 8u << 16;
                tx[2] = rx[2];
                tx[3] = rx[3];
                FDCAN1_TXBAR = 1u;
            } else {
                uart_text("CAN_BAD\n");
            }
            FDCAN1_RXF0A = (FDCAN1_RXF0S >> 8) & 0x3fu;
        }
    }
}

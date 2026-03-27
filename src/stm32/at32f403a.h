#ifndef __STM32_AT32F403A_H
#define __STM32_AT32F403A_H

#include "autoconf.h"
#include <stdint.h>

void at32f403a_clock_setup(void);
void uart_debug_print_init(uint32_t baudrate);
void mcu_can2_gpio_remap(void);
void mcu_spi4_gpio_remap(void);
#if CONFIG_STM32_SERIAL_AT_USART5_PB8_PB9 || CONFIG_STM32_USBCANBUS_PA11_PA12_AND_SERIAL_USART5_PB8_PB9
void mcu_uart_gpio_remap(void);
#endif

#ifndef TAG
  #define TAG
#endif

#if CONFIG_MACH_AT32F403A
  void at32f403a_log(char* log);
  // #define LOG_E(format, ...) at32f403a_log("[E]" TAG format, ##__VA_ARGS__)
  // #define LOG_W(format, ...) at32f403a_log("[W]" TAG format, ##__VA_ARGS__)
  // #define LOG_I(format, ...) at32f403a_log("[I]" TAG format, ##__VA_ARGS__)
  // #define LOG_V(format, ...) at32f403a_log("[V]" TAG format, ##__VA_ARGS__)

  #define LOG_E(format) at32f403a_log("[E]" TAG format)
  #define LOG_W(format) at32f403a_log("[W]" TAG format)
  #define LOG_I(format) at32f403a_log("[I]" TAG format)
  #define LOG_V(format) at32f403a_log("[V]" TAG format)
#else
  #define LOG_E(format, ...)
  #define LOG_W(format, ...)
  #define LOG_I(format, ...)
  #define LOG_V(format, ...)

#endif

#endif

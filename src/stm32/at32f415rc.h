#ifndef __STM32_AT32F415RC_H
#define __STM32_AT32F415RC_H

void at32f415rc_clock_setup(void);
void uart_debug_print_init(uint32_t baudrate);
void at32f415rc_usbotg_clock_config(void);

#ifndef TAG
  #define TAG
#endif

#if CONFIG_MACH_AT32F415
  void at32f415_log(char* log);

  #define LOG_E(format) at32f415_log("[E] " TAG format)
  #define LOG_W(format) at32f415_log("[W] " TAG format)
  #define LOG_I(format) at32f415_log("[I] " TAG format)
  #define LOG_V(format) at32f415_log("[V] " TAG format)
#else
  #define LOG_E(format, ...)
  #define LOG_W(format, ...)
  #define LOG_I(format, ...)
  #define LOG_V(format, ...)

#endif

#endif
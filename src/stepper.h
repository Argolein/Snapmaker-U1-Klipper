#ifndef __STEPPER_H
#define __STEPPER_H

#include <stdint.h> // uint8_t

struct __attribute__((aligned(2))) stepper_info {
  uint8_t type;
  uint8_t index;
  uint32_t line;
  uint32_t position;
};

uint_fast8_t stepper_event(struct timer *t);
uint16_t get_all_stepper_info(void *buffer, uint16_t max_num, uint8_t is_pl_save);
void config_stepper_print_act(uint8_t enable, uint32_t move_line);
#endif // stepper.h

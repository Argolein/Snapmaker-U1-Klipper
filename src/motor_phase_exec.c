// Motor phase executor for Snapmaker U1 — Phase 4
#include <string.h>
#include "board/irq.h"
#include "board/misc.h"
#include "basecmd.h"
#include "command.h"
#include "sched.h"
#include "spicmds.h"

#define TMC2240_DIRECT_MODE_WR  0xAD
#define PACK_DIRECT(a, b) ((((uint32_t)(int32_t)(b) & 0x1FFu) << 16) | ((uint32_t)(int32_t)(a) & 0x1FFu))
#define MPE_START_INTERVAL_MULT 16
#define MPE_STOP_INTERVAL_MULT  8
#define MPE_RAMP_INTERVAL_DIV   256
#define MPE_RAMP_SCALE_DIV      128
#define MPE_MIN_START_SCALE     12

enum {
    MPE_IDLE = 0,
    MPE_ARMED = 1,
    MPE_RAMP = 2,
    MPE_CRUISE = 3,
    MPE_DECEL = 4,
    MPE_ZERO_HOLD = 5,
    MPE_DRAIN = 6
};
#define MPE_BUFFER_SIZE 64
enum {
    MPE_MAP_SWAP_AB = 1 << 0,
    MPE_MAP_INVERT_A = 1 << 1,
    MPE_MAP_INVERT_B = 1 << 2,
};

static int16_t sine_table[1024];

struct motor_phase_exec {
    struct timer timer;
    struct spidev_s *spi;
    uint32_t interval_target;
    uint32_t interval_current;
    uint32_t ramp_step;
    uint16_t phase_index;
    uint16_t phase_offset;
    uint16_t breakaway_events;
    int16_t  phase_advance;
    uint8_t  coil_scale_current;
    uint8_t  coil_scale_target;
    uint8_t  coil_scale_cruise_target;
    uint8_t  coil_scale_step;
    uint8_t  coil_scale_decay_step;
    uint8_t  map_flags;
    uint8_t  state;
    uint8_t  zero_hold_count;
    uint8_t  last_scale_sent;
    uint16_t buffer[MPE_BUFFER_SIZE];
    uint8_t  head, tail;
    uint8_t  max_depth;
    uint16_t overflow_count;
    uint32_t event_count;
    uint32_t transfer_count;
    uint16_t last_phase_sent;
    int16_t corr_a[1024];
    int16_t corr_b[1024];
};

void mpe_config(uint32_t *args);

static void
mpe_init_tables(void)
{
    int32_t c = 32767;
    int32_t s = 0;
    const int32_t c_d = 32766;
    const int32_t s_d = 201;

    for (int i = 0; i < 1024; i++) {
        sine_table[i] = (int16_t)(s >> 1);
        int32_t c2 = ((c * c_d) - (s * s_d)) >> 15;
        int32_t s2 = ((s * c_d) + (c * s_d)) >> 15;
        c = c2;
        s = s2;
    }
}

static struct task_wake mpe_wake;

static uint8_t
mpe_buffer_depth(struct motor_phase_exec *e)
{
    return (e->head - e->tail) & (MPE_BUFFER_SIZE - 1);
}

static uint_fast8_t mpe_event(struct timer *t) {
    struct motor_phase_exec *e = container_of(t, struct motor_phase_exec, timer);
    e->event_count++;
    if (e->state == MPE_ARMED)
        e->state = MPE_RAMP;
    if (e->state != MPE_ZERO_HOLD && e->state != MPE_DRAIN)
        e->phase_index = (e->phase_index + e->phase_advance) & 1023;
    uint8_t next_head = (e->head + 1) & (MPE_BUFFER_SIZE - 1);
    if (next_head != e->tail) {
        e->buffer[e->head] = e->phase_index;
        e->head = next_head;
        uint8_t depth = mpe_buffer_depth(e);
        if (depth > e->max_depth)
            e->max_depth = depth;
    } else {
        e->overflow_count++;
    }
    sched_wake_task(&mpe_wake);
    uint32_t interval = e->interval_current;
    if (e->state == MPE_DECEL) {
        if (e->coil_scale_current > 0) {
            e->coil_scale_current = (e->coil_scale_current > e->coil_scale_step)
                ? (e->coil_scale_current - e->coil_scale_step) : 0;
        }
        uint32_t stop_interval = e->interval_target * MPE_STOP_INTERVAL_MULT;
        if (e->interval_current < stop_interval) {
            uint32_t next = e->interval_current + e->ramp_step;
            if (next > stop_interval)
                next = stop_interval;
            e->interval_current = next;
        }
        if (e->coil_scale_current == 0 && e->interval_current >= stop_interval) {
            e->state = MPE_ZERO_HOLD;
            e->zero_hold_count = 32;
            e->interval_current = stop_interval;
        }
    } else if (e->state == MPE_ZERO_HOLD) {
        e->coil_scale_current = 0;
        if (e->zero_hold_count)
            e->zero_hold_count--;
        if (!e->zero_hold_count) {
            e->state = MPE_DRAIN;
            return SF_DONE;
        }
    } else {
        if (e->state != MPE_CRUISE
            && e->coil_scale_current < e->coil_scale_target) {
            uint16_t next_scale = e->coil_scale_current + e->coil_scale_step;
            if (next_scale > e->coil_scale_target)
                next_scale = e->coil_scale_target;
            e->coil_scale_current = next_scale;
        }
        if (e->interval_current > e->interval_target) {
            uint32_t next = e->interval_current - e->ramp_step;
            if (next < e->interval_target)
                next = e->interval_target;
            e->interval_current = next;
            if (e->interval_current == e->interval_target)
                e->state = MPE_CRUISE;
        } else if (e->state == MPE_CRUISE) {
            if (e->breakaway_events) {
                e->breakaway_events--;
            } else if (e->coil_scale_current > e->coil_scale_cruise_target) {
                uint8_t next_scale = (e->coil_scale_current > e->coil_scale_decay_step)
                    ? (e->coil_scale_current - e->coil_scale_decay_step) : 0;
                if (next_scale < e->coil_scale_cruise_target)
                    next_scale = e->coil_scale_cruise_target;
                e->coil_scale_current = next_scale;
            }
        }
    }
    t->waketime += interval;
    return SF_RESCHEDULE;
}

void mpe_task(void) {
    if (!sched_check_wake(&mpe_wake)) return;
    uint8_t oid = 0xff;
    struct motor_phase_exec *e;
    uint8_t need_resched = 0;
    while ((e = oid_next(&oid, mpe_config))) {
        for (int i = 0; i < 8 && e->tail != e->head; i++) {
            uint16_t pi = e->buffer[e->tail];
            e->tail = (e->tail + 1) & (MPE_BUFFER_SIZE - 1);
            if (e->state == MPE_IDLE)
                continue;
            uint8_t scale = e->coil_scale_current;
            uint16_t lpi = (pi + e->phase_offset) & 1023;
            int32_t sa = (int32_t)sine_table[lpi] + e->corr_a[lpi];
            int32_t sb = (int32_t)sine_table[(lpi + 256) & 1023]
                + e->corr_b[lpi];
            if (e->map_flags & MPE_MAP_SWAP_AB) {
                int32_t tmp = sa;
                sa = sb;
                sb = tmp;
            }
            if (e->map_flags & MPE_MAP_INVERT_A)
                sa = -sa;
            if (e->map_flags & MPE_MAP_INVERT_B)
                sb = -sb;
            int16_t ca = (int16_t)((sa * scale) >> 14);
            int16_t cb = (int16_t)((sb * scale) >> 14);
            // TMC xdirect mode uses swapped coil ordering.
            int16_t tmp = ca;
            ca = cb;
            cb = tmp;
            uint32_t val = PACK_DIRECT(ca, cb);
            uint8_t buf[5] = {
                TMC2240_DIRECT_MODE_WR,
                (uint8_t)(val >> 24), (uint8_t)(val >> 16),
                (uint8_t)(val >> 8), (uint8_t)(val)
            };
            spidev_transfer(e->spi, 0, sizeof(buf), buf);
            e->transfer_count++;
            e->last_phase_sent = pi;
            e->last_scale_sent = scale;
        }
        if (e->state == MPE_DRAIN && e->tail == e->head)
            e->state = MPE_IDLE;
        if (e->tail != e->head)
            need_resched = 1;
    }
    if (need_resched)
        sched_wake_task(&mpe_wake);
}
DECL_TASK(mpe_task);

void mpe_config(uint32_t *args) {
    struct motor_phase_exec *e = oid_alloc(args[0], mpe_config, sizeof(*e));
    e->timer.func = mpe_event;
    e->spi = spidev_oid_lookup(args[1]);
    e->head = e->tail = 0;
    e->state = MPE_IDLE;
}
DECL_COMMAND(mpe_config, "config_mpe oid=%c spi_oid=%c");

void mpe_update(uint32_t *args) {
    struct motor_phase_exec *e = oid_lookup(args[0], mpe_config);
    uint16_t offset = args[1];
    uint8_t len = args[2];
    uint8_t *data = command_decode_ptr(args[3]);
    uint8_t table_id = args[4];
    for (int i = 0; i < (len / 2) && (offset + i) < 1024; i++) {
        int16_t value = (int16_t)((uint16_t)data[i * 2]
                                  | ((uint16_t)data[i * 2 + 1] << 8));
        if (table_id == 0)
            e->corr_a[offset + i] = value;
        else
            e->corr_b[offset + i] = value;
    }
}
DECL_COMMAND(mpe_update, "mpe_update oid=%c offset=%u data=%*s table=%c");

void mpe_clear(uint32_t *args) {
    struct motor_phase_exec *e = oid_lookup(args[0], mpe_config);
    memset(e->corr_a, 0, sizeof(e->corr_a));
    memset(e->corr_b, 0, sizeof(e->corr_b));
}
DECL_COMMAND(mpe_clear, "mpe_clear oid=%c");

void mpe_query(uint32_t *args) {
    uint8_t oid = args[0];
    struct motor_phase_exec *e = oid_lookup(oid, mpe_config);
    uint8_t state;
    uint16_t phase_index;
    uint8_t coil_scale;
    uint32_t interval;
    uint8_t depth;
    uint8_t max_depth;
    uint16_t overflow_count;
    uint32_t event_count;
    uint32_t transfer_count;
    uint16_t last_phase_sent;
    uint8_t last_scale_sent;
    irq_disable();
    state = e->state;
    phase_index = e->phase_index;
    coil_scale = e->coil_scale_current;
    interval = e->interval_current;
    depth = mpe_buffer_depth(e);
    max_depth = e->max_depth;
    overflow_count = e->overflow_count;
    event_count = e->event_count;
    transfer_count = e->transfer_count;
    last_phase_sent = e->last_phase_sent;
    last_scale_sent = e->last_scale_sent;
    irq_enable();
    sendf("mpe_state oid=%c state=%c phase_index=%u coil_scale=%c interval=%u"
          " depth=%c max_depth=%c overflow_count=%u event_count=%u"
          " transfer_count=%u last_phase_sent=%u last_scale_sent=%c",
          oid, state, phase_index, coil_scale, interval,
          depth, max_depth, overflow_count, event_count,
          transfer_count, last_phase_sent, last_scale_sent);
}
DECL_COMMAND(mpe_query, "mpe_query oid=%c");

void mpe_start(uint32_t *args) {
    struct motor_phase_exec *e = oid_lookup(args[0], mpe_config);
    if (e->state != MPE_IDLE)
        sched_del_timer(&e->timer);
    e->interval_target = args[1];
    e->interval_current = e->interval_target * MPE_START_INTERVAL_MULT;
    if (e->interval_current < e->interval_target)
        e->interval_current = e->interval_target;
    uint32_t ramp_delta = e->interval_current - e->interval_target;
    e->ramp_step = ramp_delta ? ramp_delta / MPE_RAMP_INTERVAL_DIV : 0;
    if (!e->ramp_step)
        e->ramp_step = 1;
    e->phase_index = (uint16_t)(args[2] & 1023);
    e->coil_scale_target = (uint8_t)args[3];
    e->coil_scale_cruise_target = (uint8_t)args[4];
    e->breakaway_events = (uint16_t)args[5];
    e->coil_scale_current = e->coil_scale_target / 8;
    if (e->coil_scale_current < MPE_MIN_START_SCALE)
        e->coil_scale_current = MPE_MIN_START_SCALE;
    if (e->coil_scale_current > e->coil_scale_target)
        e->coil_scale_current = e->coil_scale_target;
    uint16_t scale_delta = e->coil_scale_target - e->coil_scale_current;
    e->coil_scale_step = scale_delta ? scale_delta / MPE_RAMP_SCALE_DIV : 0;
    if (!e->coil_scale_step)
        e->coil_scale_step = 1;
    uint16_t decay_delta = e->coil_scale_target - e->coil_scale_cruise_target;
    e->coil_scale_decay_step = decay_delta ? decay_delta / 32 : 0;
    if (!e->coil_scale_decay_step)
        e->coil_scale_decay_step = 1;
    uint32_t start_clock = args[6];
    e->phase_advance = (int16_t)args[7];
    e->phase_offset = (uint16_t)(args[8] & 1023);
    e->map_flags = (uint8_t)args[9];
    e->head = e->tail = 0;
    e->zero_hold_count = 0;
    e->last_scale_sent = 0;
    e->max_depth = 0;
    e->overflow_count = 0;
    e->event_count = 0;
    e->transfer_count = 0;
    e->last_phase_sent = e->phase_index;
    e->state = MPE_ARMED;
    irq_disable();
    e->timer.waketime = start_clock;
    sched_add_timer(&e->timer); irq_enable();
}
DECL_COMMAND(mpe_start, "mpe_start oid=%c interval=%u phase_index=%u"
    " breakaway_scale=%c cruise_scale=%c breakaway_events=%hu"
    " start_clock=%u phase_advance=%hi phase_offset=%u map_flags=%c");

void mpe_stop(uint32_t *args) {
    struct motor_phase_exec *e = oid_lookup(args[0], mpe_config);
    if (e->state == MPE_IDLE)
        return;
    if (e->state == MPE_ARMED) {
        sched_del_timer(&e->timer);
        e->state = MPE_IDLE;
        e->head = e->tail = 0;
        e->zero_hold_count = 0;
        return;
    }
    e->state = MPE_DECEL;
}
DECL_COMMAND(mpe_stop, "mpe_stop oid=%c");

void mpe_setup_dummy(void) { mpe_init_tables(); }
DECL_INIT(mpe_setup_dummy);

void mpe_shutdown(void) {
    uint8_t oid = 0xff;
    struct motor_phase_exec *e;
    while ((e = oid_next(&oid, mpe_config))) {
        e->state = MPE_IDLE;
        e->head = e->tail = 0;
        e->zero_hold_count = 0;
    }
}
DECL_SHUTDOWN(mpe_shutdown);

// Motor phase executor for Snapmaker U1 — baseline-only prototype
//
// Drives TMC2240 DIRECT_MODE coil currents at a fixed phase-update rate from a
// MCU timer task.  Designed for isolated test moves only; not for production
// print use.
//
// Architecture:
//   Timer ISR fires at the configured rate, advances the phase index, sets a
//   pending flag, and wakes the task.  The task (main-loop context) drains the
//   flag and writes one 5-byte SPI frame to the TMC2240 DIRECT_MODE register.
//   This split is required because spidev_transfer() is blocking and must not
//   be called from a timer ISR.
//
// Copyright (C) 2026  Snapmaker U1 Klipper contributors
// This file may be distributed under the terms of the GNU GPLv3 license.

#include "board/irq.h"   // irq_disable / irq_enable
#include "board/misc.h"  // timer_read_time
#include "basecmd.h"     // oid_alloc, oid_lookup, foreach_oid
#include "command.h"     // DECL_COMMAND
#include "sched.h"       // DECL_TASK, DECL_SHUTDOWN, DECL_INIT, struct timer
#include "spicmds.h"     // spidev_oid_lookup, spidev_transfer

// TMC2240 DIRECT_MODE register write address (write-bit 0x80 | register 0x2D)
#define TMC2240_DIRECT_MODE_WR  0xAD

// Pack two 9-bit signed coil values into a 32-bit DIRECT_MODE register value.
//   coil_a occupies bits [8:0], coil_b bits [24:16].
//   The 0x1FFu mask extracts the 9-bit two's complement representation.
#define PACK_DIRECT(a, b) \
    ((((uint32_t)(int32_t)(b) & 0x1FFu) << 16) | \
      ((uint32_t)(int32_t)(a) & 0x1FFu))

enum { MPE_IDLE = 0, MPE_RUNNING = 1 };

struct motor_phase_exec {
    struct timer timer;
    struct spidev_s *spi;
    uint32_t interval;       // timer interval in MCU clock ticks
    uint16_t phase_index;    // current phase position, 0..1023
    uint8_t  phase_advance;  // phase_index increment per timer tick
    uint8_t  coil_scale;     // current amplitude, 0..255
    uint8_t  state;          // MPE_IDLE or MPE_RUNNING
    uint8_t  pending;        // set by timer ISR, cleared by task
};

// Baseline 1024-point tables in Q14 fixed-point (1.0 == 16384):
//   bl_cos[i] = round(cos(2*pi*i/1024) * 16384)
//   bl_sin[i] = round(sin(2*pi*i/1024) * 16384)
// Computed at init time via Q15 recursive rotation; no float needed at runtime.
static int16_t bl_cos[1024];
static int16_t bl_sin[1024];

static struct task_wake mpe_wake;

// Forward declaration — defined after the config command that references it
static uint_fast8_t motor_phase_exec_event(struct timer *t);

// Populate baseline tables once at firmware startup (not in ISR).
//
// Method: rotate a unit vector by Δθ = 2π/1024 per step using integer Q15
// arithmetic.
//   cos(Δθ) = 0.999981175 → Q15: 32766
//   sin(Δθ) = 0.006135885 → Q15: 201
//
// Magnitude drift over 1024 steps is ≈ −1.3%, which is acceptable for a
// prototype baseline comparison.
void
motor_phase_exec_init(void)
{
    int32_t c = 32767;  // cos(0) in Q15 (= 1.0)
    int32_t s = 0;      // sin(0) in Q15 (= 0.0)
    const int32_t c_d = 32766;  // cos(2π/1024) in Q15
    const int32_t s_d = 201;    // sin(2π/1024) in Q15

    for (int i = 0; i < 1024; i++) {
        // Convert Q15 → Q14 by right-shifting 1; store as int16_t
        bl_cos[i] = (int16_t)(c >> 1);
        bl_sin[i] = (int16_t)(s >> 1);

        // Rotate: [c', s'] = [c*cd - s*sd, s*cd + c*sd] in Q15
        int32_t c2 = ((c * c_d) - (s * s_d)) >> 15;
        int32_t s2 = ((s * c_d) + (c * s_d)) >> 15;
        c = c2;
        s = s2;
    }
}
DECL_INIT(motor_phase_exec_init);

// Config command: allocate the executor object and link its SPI device.
void
command_config_motor_phase_exec(uint32_t *args)
{
    struct motor_phase_exec *e = oid_alloc(
        args[0], command_config_motor_phase_exec, sizeof(*e));
    e->timer.func = motor_phase_exec_event;  // defined below
    e->spi = spidev_oid_lookup(args[1]);
}
DECL_COMMAND(command_config_motor_phase_exec,
             "config_motor_phase_exec oid=%c spi_oid=%c");

// Timer ISR: advance phase, flag task, reschedule.
// Must not call spidev_transfer() — SPI is blocking.
static uint_fast8_t
motor_phase_exec_event(struct timer *t)
{
    struct motor_phase_exec *e =
        container_of(t, struct motor_phase_exec, timer);
    e->phase_index = (e->phase_index + e->phase_advance) & 1023;
    e->pending = 1;
    sched_wake_task(&mpe_wake);
    t->waketime += e->interval;
    return SF_RESCHEDULE;
}

// Main-loop task: drain the pending flag and write one DIRECT_MODE SPI frame.
void
motor_phase_exec_task(void)
{
    if (!sched_check_wake(&mpe_wake))
        return;
    uint8_t oid;
    struct motor_phase_exec *e;
    foreach_oid(oid, e, command_config_motor_phase_exec) {
        if (!e->pending)
            continue;
        e->pending = 0;
        if (e->state != MPE_RUNNING)
            continue;

        // Compute scaled coil currents from the baseline Q14 tables.
        // Scale: (Q14 * uint8) >> 14 yields a value in −255..+255.
        uint16_t pi    = e->phase_index;
        uint8_t  scale = e->coil_scale;
        int16_t  ca    = (int16_t)(((int32_t)bl_cos[pi] * scale) >> 14);
        int16_t  cb    = (int16_t)(((int32_t)bl_sin[pi] * scale) >> 14);

        // Pack into the TMC2240 DIRECT_MODE 32-bit register value and send.
        uint32_t val = PACK_DIRECT(ca, cb);
        uint8_t buf[5] = {
            TMC2240_DIRECT_MODE_WR,
            (uint8_t)(val >> 24),
            (uint8_t)(val >> 16),
            (uint8_t)(val >>  8),
            (uint8_t)(val),
        };
        spidev_transfer(e->spi, 0, sizeof(buf), buf);
    }
}
DECL_TASK(motor_phase_exec_task);

// Start command: configure and arm the phase-update timer.
void
command_motor_phase_exec_start(uint32_t *args)
{
    struct motor_phase_exec *e = oid_lookup(
        args[0], command_config_motor_phase_exec);

    // If already running, stop the old timer before starting fresh.
    if (e->state == MPE_RUNNING)
        sched_del_timer(&e->timer);

    e->interval      = args[1];
    e->phase_index   = (uint16_t)(args[2] & 1023);
    e->coil_scale    = (uint8_t)args[3];
    e->phase_advance = (uint8_t)args[4];
    e->pending       = 0;
    e->state         = MPE_RUNNING;

    irq_disable();
    e->timer.waketime = timer_read_time() + e->interval;
    sched_add_timer(&e->timer);
    irq_enable();
}
DECL_COMMAND(command_motor_phase_exec_start,
             "motor_phase_exec_start oid=%c interval=%u phase_index=%u"
             " coil_scale=%c phase_advance=%c");

// Stop command: cancel the timer and go idle.
void
command_motor_phase_exec_stop(uint32_t *args)
{
    struct motor_phase_exec *e = oid_lookup(
        args[0], command_config_motor_phase_exec);
    if (e->state == MPE_RUNNING) {
        sched_del_timer(&e->timer);
        e->state   = MPE_IDLE;
        e->pending = 0;
    }
}
DECL_COMMAND(command_motor_phase_exec_stop,
             "motor_phase_exec_stop oid=%c");

// Shutdown handler: mark all executors idle so the task stops writing.
// Timer cancellation is not safe here; the timer will expire harmlessly
// because state is already IDLE when the task next checks.
void
motor_phase_exec_shutdown(void)
{
    uint8_t oid;
    struct motor_phase_exec *e;
    foreach_oid(oid, e, command_config_motor_phase_exec) {
        e->state   = MPE_IDLE;
        e->pending = 0;
    }
}
DECL_SHUTDOWN(motor_phase_exec_shutdown);

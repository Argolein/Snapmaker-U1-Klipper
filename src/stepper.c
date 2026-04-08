// Handling of stepper drivers.
//
// Copyright (C) 2016-2021  Kevin O'Connor <kevin@koconnor.net>
//
// This file may be distributed under the terms of the GNU GPLv3 license.

#include "autoconf.h" // CONFIG_*
#include "basecmd.h" // oid_alloc
#include "board/gpio.h" // gpio_out_write
#include "board/irq.h" // irq_disable
#include "board/misc.h" // timer_is_before
#include "command.h" // DECL_COMMAND
#include "sched.h" // struct timer
#include "stepper.h" // stepper_event
#include "trsync.h" // trsync_add_signal

#if CONFIG_INLINE_STEPPER_HACK && CONFIG_HAVE_STEPPER_BOTH_EDGE
 #define HAVE_SINGLE_SCHEDULE 1
 #define HAVE_EDGE_OPTIMIZATION 1
 #define HAVE_AVR_OPTIMIZATION 0
 DECL_CONSTANT("STEPPER_BOTH_EDGE", 1);
#elif CONFIG_INLINE_STEPPER_HACK && CONFIG_MACH_AVR
 #define HAVE_SINGLE_SCHEDULE 1
 #define HAVE_EDGE_OPTIMIZATION 0
 #define HAVE_AVR_OPTIMIZATION 1
#else
 #define HAVE_SINGLE_SCHEDULE 0
 #define HAVE_EDGE_OPTIMIZATION 0
 #define HAVE_AVR_OPTIMIZATION 0
#endif

struct stepper_move {
    struct move_node node;
    uint32_t interval;
    int16_t add;
    uint16_t count;
    uint8_t flags;
    uint32_t line;
};

enum { MF_DIR=1<<0 };
enum { DEBUG_EXEC_TRACE_MAX = 64 };

struct stepper_exec_sample {
    uint32_t step_clock;
    uint32_t step_number;
};

struct stepper {
    struct timer time;
    uint8_t type;
    uint8_t index;
    uint8_t print_act;
    uint32_t move_line;
    uint32_t interval;
    int16_t add;
    uint32_t count;
    uint32_t next_step_time, step_pulse_ticks;
    struct gpio_out step_pin, dir_pin;
    uint32_t position;
    uint32_t debug_queue_msgs;
    uint32_t debug_load_next;
    uint32_t debug_timer_events;
    uint32_t debug_total_steps;
    uint16_t debug_max_chunk;
    uint16_t debug_exec_trace_stride;
    uint16_t debug_exec_trace_count;
    uint32_t debug_exec_next_sample;
    uint32_t debug_exec_first_clock;
    uint32_t debug_exec_last_clock;
    uint32_t debug_exec_min_interval;
    uint32_t debug_exec_max_interval;
    struct stepper_exec_sample debug_exec_trace[DEBUG_EXEC_TRACE_MAX];
    struct move_queue_head mq;
    struct trsync_signal stop_signal;
    // gcc (pre v6) does better optimization when uint8_t are bitfields
    uint8_t flags : 8;
};

enum { POSITION_BIAS=0x40000000 };

enum {
    SF_LAST_DIR=1<<0, SF_NEXT_DIR=1<<1, SF_INVERT_STEP=1<<2, SF_NEED_RESET=1<<3,
    SF_SINGLE_SCHED=1<<4, SF_HAVE_ADD=1<<5
};

static uint32_t
stepper_queue_depth(struct stepper *s)
{
    uint32_t depth = 0;
    struct move_node *mn = move_queue_first(&s->mq);
    while (mn) {
        depth++;
        mn = mn->next;
    }
    return depth;
}

static void
stepper_debug_reset(struct stepper *s)
{
    s->debug_queue_msgs = 0;
    s->debug_load_next = 0;
    s->debug_timer_events = 0;
    s->debug_total_steps = 0;
    s->debug_max_chunk = 0;
    s->debug_exec_trace_count = 0;
    s->debug_exec_next_sample = 0;
    s->debug_exec_first_clock = 0;
    s->debug_exec_last_clock = 0;
    s->debug_exec_min_interval = 0;
    s->debug_exec_max_interval = 0;
}

static void
stepper_debug_note_exec(struct stepper *s, uint32_t step_clock)
{
    uint16_t stride = s->debug_exec_trace_stride;
    if (!stride)
        return;
    uint32_t total_steps = s->debug_total_steps;
    if (!total_steps)
        return;
    uint32_t step_number = total_steps - 1;
    if (!s->debug_exec_trace_count) {
        s->debug_exec_first_clock = step_clock;
        s->debug_exec_last_clock = step_clock;
    } else {
        uint32_t interval = step_clock - s->debug_exec_last_clock;
        if (!s->debug_exec_min_interval || interval < s->debug_exec_min_interval)
            s->debug_exec_min_interval = interval;
        if (interval > s->debug_exec_max_interval)
            s->debug_exec_max_interval = interval;
        s->debug_exec_last_clock = step_clock;
    }
    if (step_number < s->debug_exec_next_sample)
        return;
    if (s->debug_exec_trace_count < DEBUG_EXEC_TRACE_MAX) {
        struct stepper_exec_sample *sample;
        sample = &s->debug_exec_trace[s->debug_exec_trace_count++];
        sample->step_clock = step_clock;
        sample->step_number = step_number;
    }
    s->debug_exec_next_sample = step_number + stride;
}

// Setup a stepper for the next move in its queue
static uint_fast8_t
stepper_load_next(struct stepper *s)
{
    if (move_queue_empty(&s->mq)) {
        // There is no next move - the queue is empty
        s->count = 0;
        return SF_DONE;
    }

    // Load next 'struct stepper_move' into 'struct stepper'
    struct move_node *mn = move_queue_pop(&s->mq);
    struct stepper_move *m = container_of(mn, struct stepper_move, node);
    s->debug_load_next++;
    if (m->count > s->debug_max_chunk)
        s->debug_max_chunk = m->count;
    s->add = m->add;
    s->interval = m->interval + m->add;
    if (HAVE_SINGLE_SCHEDULE && s->flags & SF_SINGLE_SCHED) {
        s->time.waketime += m->interval;
        if (HAVE_AVR_OPTIMIZATION)
            s->flags = m->add ? s->flags|SF_HAVE_ADD : s->flags & ~SF_HAVE_ADD;
        s->count = m->count;
    } else {
        // It is necessary to schedule unstep events and so there are
        // twice as many events.
        s->next_step_time += m->interval;
        s->time.waketime = s->next_step_time;
        s->count = (uint32_t)m->count * 2;
    }
    // Add all steps to s->position (stepper_get_position() can calc mid-move)
    if (m->flags & MF_DIR) {
        s->position = -s->position + m->count;
        gpio_out_toggle_noirq(s->dir_pin);
    } else {
        s->position += m->count;
    }
    if (m->line != 0 && m->line != 0xFFFFFFFF && s->print_act)
        s->move_line = m->line;
    move_free(m);
    return SF_RESCHEDULE;
}

// Optimized step function to step on each step pin edge
uint_fast8_t
stepper_event_edge(struct timer *t)
{
    struct stepper *s = container_of(t, struct stepper, time);
    s->debug_timer_events++;
    s->debug_total_steps++;
    stepper_debug_note_exec(s, timer_read_time());
    gpio_out_toggle_noirq(s->step_pin);
    uint32_t count = s->count - 1;
    if (likely(count)) {
        s->count = count;
        s->time.waketime += s->interval;
        s->interval += s->add;
        return SF_RESCHEDULE;
    }
    return stepper_load_next(s);
}

#define AVR_STEP_INSNS 40 // minimum instructions between step gpio pulses

// AVR optimized step function
static uint_fast8_t
stepper_event_avr(struct timer *t)
{
    struct stepper *s = container_of(t, struct stepper, time);
    s->debug_timer_events++;
    s->debug_total_steps++;
    stepper_debug_note_exec(s, timer_read_time());
    gpio_out_toggle_noirq(s->step_pin);
    uint16_t *pcount = (void*)&s->count, count = *pcount - 1;
    if (likely(count)) {
        *pcount = count;
        s->time.waketime += s->interval;
        gpio_out_toggle_noirq(s->step_pin);
        if (s->flags & SF_HAVE_ADD)
            s->interval += s->add;
        return SF_RESCHEDULE;
    }
    uint_fast8_t ret = stepper_load_next(s);
    gpio_out_toggle_noirq(s->step_pin);
    return ret;
}

// Regular "double scheduled" step function
uint_fast8_t
stepper_event_full(struct timer *t)
{
    struct stepper *s = container_of(t, struct stepper, time);
    uint32_t current_count = s->count;
    s->debug_timer_events++;
    if (!(current_count & 1)) {
        s->debug_total_steps++;
        stepper_debug_note_exec(s, timer_read_time());
    }
    gpio_out_toggle_noirq(s->step_pin);
    uint32_t curtime = timer_read_time();
    uint32_t min_next_time = curtime + s->step_pulse_ticks;
    s->count--;
    if (likely(s->count & 1))
        // Schedule unstep event
        goto reschedule_min;
    if (likely(s->count)) {
        s->next_step_time += s->interval;
        s->interval += s->add;
        if (unlikely(timer_is_before(s->next_step_time, min_next_time)))
            // The next step event is too close - push it back
            goto reschedule_min;
        s->time.waketime = s->next_step_time;
        return SF_RESCHEDULE;
    }
    uint_fast8_t ret = stepper_load_next(s);
    if (ret == SF_DONE || !timer_is_before(s->time.waketime, min_next_time))
        return ret;
    // Next step event is too close to the last unstep
    int32_t diff = s->time.waketime - min_next_time;
    if (diff < (int32_t)-timer_from_us(1000))
        shutdown("Stepper too far in past");
reschedule_min:
    s->time.waketime = min_next_time;
    return SF_RESCHEDULE;
}

// Optimized entry point for step function (may be inlined into sched.c code)
uint_fast8_t
stepper_event(struct timer *t)
{
    if (HAVE_EDGE_OPTIMIZATION)
        return stepper_event_edge(t);
    if (HAVE_AVR_OPTIMIZATION)
        return stepper_event_avr(t);
    return stepper_event_full(t);
}

void
command_config_stepper(uint32_t *args)
{
    struct stepper *s = oid_alloc(args[0], command_config_stepper, sizeof(*s));
    int_fast8_t invert_step = args[3];
    s->flags = invert_step > 0 ? SF_INVERT_STEP : 0;
    s->step_pin = gpio_out_setup(args[1], s->flags & SF_INVERT_STEP);
    s->dir_pin = gpio_out_setup(args[2], 0);
    s->position = -POSITION_BIAS;
    s->step_pulse_ticks = args[4];
    s->move_line = 0xFFFFFFFF;
    s->type = args[5];
    s->index = args[6];
    s->print_act = 0;
    move_queue_setup(&s->mq, sizeof(struct stepper_move));
    if (HAVE_EDGE_OPTIMIZATION) {
        if (!s->step_pulse_ticks && invert_step < 0)
            s->flags |= SF_SINGLE_SCHED;
        else
            s->time.func = stepper_event_full;
    } else if (HAVE_AVR_OPTIMIZATION) {
        if (s->step_pulse_ticks <= AVR_STEP_INSNS)
            s->flags |= SF_SINGLE_SCHED;
        else
            s->time.func = stepper_event_full;
    } else if (!CONFIG_INLINE_STEPPER_HACK) {
        s->time.func = stepper_event_full;
    }
}
DECL_COMMAND(command_config_stepper,
             "config_stepper oid=%c step_pin=%c dir_pin=%c invert_step=%c"
             " step_pulse_ticks=%u type=%u index=%u");

// Motor Phase Executor Infiltration
// Return the 'struct stepper' for a given stepper oid
static struct stepper *
stepper_oid_lookup(uint8_t oid)
{
    return oid_lookup(oid, command_config_stepper);
}

// Schedule a set of steps with a given timing
void
command_queue_step(uint32_t *args)
{
    struct stepper *s = stepper_oid_lookup(args[0]);
    struct stepper_move *m = move_alloc();
    m->interval = args[1];
    m->count = args[2];
    if (!m->count)
        shutdown("Invalid count parameter");
    m->add = args[3];
    m->flags = 0;
    m->line = args[4];

    irq_disable();
    s->debug_queue_msgs++;
    if (m->count > s->debug_max_chunk)
        s->debug_max_chunk = m->count;
    uint8_t flags = s->flags;
    if (!!(flags & SF_LAST_DIR) != !!(flags & SF_NEXT_DIR)) {
        flags ^= SF_LAST_DIR;
        m->flags |= MF_DIR;
    }
    if (s->count) {
        s->flags = flags;
        move_queue_push(&m->node, &s->mq);
    } else if (flags & SF_NEED_RESET) {
        move_free(m);
    } else {
        s->flags = flags;
        move_queue_push(&m->node, &s->mq);
        stepper_load_next(s);
        sched_add_timer(&s->time);
    }
    irq_enable();
}
DECL_COMMAND(command_queue_step,
             "queue_step oid=%c interval=%u count=%hu add=%hi line=%u");

void
command_stepper_runtime_reset(uint32_t *args)
{
    struct stepper *s = stepper_oid_lookup(args[0]);
    irq_disable();
    s->debug_exec_trace_stride = 0;
    stepper_debug_reset(s);
    irq_enable();
}
DECL_COMMAND(command_stepper_runtime_reset, "stepper_runtime_reset oid=%c");

void
command_stepper_runtime_query(uint32_t *args)
{
    uint8_t oid = args[0];
    struct stepper *s = stepper_oid_lookup(oid);
    uint32_t queue_msgs, load_next, timer_events, total_steps, queued_moves;
    uint16_t max_chunk;
    irq_disable();
    queue_msgs = s->debug_queue_msgs;
    load_next = s->debug_load_next;
    timer_events = s->debug_timer_events;
    total_steps = s->debug_total_steps;
    max_chunk = s->debug_max_chunk;
    queued_moves = stepper_queue_depth(s);
    irq_enable();
    sendf("stepper_runtime_state oid=%c queue_msgs=%u load_next=%u"
          " timer_events=%u total_steps=%u max_chunk=%hu queued_moves=%u",
          oid, queue_msgs, load_next, timer_events, total_steps,
          max_chunk, queued_moves);
}
DECL_COMMAND(command_stepper_runtime_query, "stepper_runtime_query oid=%c");

void
command_stepper_exec_trace_reset(uint32_t *args)
{
    struct stepper *s = stepper_oid_lookup(args[0]);
    irq_disable();
    s->debug_exec_trace_stride = args[1];
    stepper_debug_reset(s);
    irq_enable();
}
DECL_COMMAND(command_stepper_exec_trace_reset,
             "stepper_exec_trace_reset oid=%c stride=%hu");

void
command_stepper_exec_trace_query(uint32_t *args)
{
    uint8_t oid = args[0];
    struct stepper *s = stepper_oid_lookup(oid);
    uint32_t total_steps, first_clock, last_clock, min_interval, max_interval;
    uint16_t stride, count;
    irq_disable();
    stride = s->debug_exec_trace_stride;
    count = s->debug_exec_trace_count;
    total_steps = s->debug_total_steps;
    first_clock = s->debug_exec_first_clock;
    last_clock = s->debug_exec_last_clock;
    min_interval = s->debug_exec_min_interval;
    max_interval = s->debug_exec_max_interval;
    irq_enable();
    sendf("stepper_exec_trace_state oid=%c stride=%hu count=%hu"
          " total_steps=%u first_clock=%u last_clock=%u"
          " min_interval=%u max_interval=%u",
          oid, stride, count, total_steps, first_clock, last_clock,
          min_interval, max_interval);
}
DECL_COMMAND(command_stepper_exec_trace_query,
             "stepper_exec_trace_query oid=%c");

void
command_stepper_exec_trace_sample(uint32_t *args)
{
    uint8_t oid = args[0];
    uint8_t index = args[1];
    struct stepper *s = stepper_oid_lookup(oid);
    struct stepper_exec_sample sample;
    uint16_t count;
    irq_disable();
    count = s->debug_exec_trace_count;
    sample.step_clock = 0;
    sample.step_number = 0;
    if (index < count)
        sample = s->debug_exec_trace[index];
    irq_enable();
    sendf("stepper_exec_trace_point oid=%c index=%c step_clock=%u"
          " step_number=%u",
          oid, index, sample.step_clock, sample.step_number);
}
DECL_COMMAND(command_stepper_exec_trace_sample,
             "stepper_exec_trace_sample oid=%c index=%c");

// Set the direction of the next queued step
void
command_set_next_step_dir(uint32_t *args)
{
    struct stepper *s = stepper_oid_lookup(args[0]);
    uint8_t nextdir = args[1] ? SF_NEXT_DIR : 0;
    irq_disable();
    s->flags = (s->flags & ~SF_NEXT_DIR) | nextdir;
    irq_enable();
}
DECL_COMMAND(command_set_next_step_dir, "set_next_step_dir oid=%c dir=%c");

// Set an absolute time that the next step will be relative to
void
command_reset_step_clock(uint32_t *args)
{
    struct stepper *s = stepper_oid_lookup(args[0]);
    uint32_t waketime = args[1];
    irq_disable();
    if (s->count)
        shutdown("Can't reset time when stepper active");
    s->next_step_time = s->time.waketime = waketime;
    s->flags &= ~SF_NEED_RESET;
    irq_enable();
}
DECL_COMMAND(command_reset_step_clock, "reset_step_clock oid=%c clock=%u");

// Return the current stepper position.  Caller must disable irqs.
static uint32_t
stepper_get_position(struct stepper *s)
{
    uint32_t position = s->position;
    // If stepper is mid-move, subtract out steps not yet taken
    if (HAVE_SINGLE_SCHEDULE && s->flags & SF_SINGLE_SCHED)
        position -= s->count;
    else
        position -= s->count / 2;
    // The top bit of s->position is an optimized reverse direction flag
    if (position & 0x80000000)
        return -position;
    return position;
}

// Report the current position of the stepper
void
command_stepper_get_position(uint32_t *args)
{
    uint8_t oid = args[0];
    struct stepper *s = stepper_oid_lookup(oid);
    irq_disable();
    uint32_t position = stepper_get_position(s);
    irq_enable();
    sendf("stepper_position oid=%c pos=%i", oid, position - POSITION_BIAS);
}
DECL_COMMAND(command_stepper_get_position, "stepper_get_position oid=%c");

// Stop all moves for a given stepper (caller must disable IRQs)
static void
stepper_stop(struct trsync_signal *tss, uint8_t reason)
{
    struct stepper *s = container_of(tss, struct stepper, stop_signal);
    sched_del_timer(&s->time);
    s->next_step_time = s->time.waketime = 0;
    s->position = -stepper_get_position(s);
    s->count = 0;
    s->flags = (s->flags & (SF_INVERT_STEP|SF_SINGLE_SCHED)) | SF_NEED_RESET;
    gpio_out_write(s->dir_pin, 0);
    if (!(HAVE_EDGE_OPTIMIZATION && s->flags & SF_SINGLE_SCHED))
        gpio_out_write(s->step_pin, s->flags & SF_INVERT_STEP);
    while (!move_queue_empty(&s->mq)) {
        struct move_node *mn = move_queue_pop(&s->mq);
        struct stepper_move *m = container_of(mn, struct stepper_move, node);
        move_free(m);
    }
}

uint16_t get_all_stepper_info(void *buffer, uint16_t max_num, uint8_t is_pl_save) {
    struct stepper *s;
    uint8_t i;
    uint16_t len = 0;
    if (buffer != NULL) {
        foreach_oid(i, s, command_config_stepper) {
            if (len < max_num && s->type != 0xFF) {
                (((struct stepper_info *)buffer) + len)->type  = s->type;
                (((struct stepper_info *)buffer) + len)->index = s->index;
                (((struct stepper_info *)buffer) + len)->line  = s->move_line;
                (((struct stepper_info *)buffer) + len)->position = stepper_get_position(s) - POSITION_BIAS;
                len++;
            }

            if (is_pl_save) {
                move_queue_clear(&s->mq);
                stepper_stop(&s->stop_signal, 0);
            }
        }
    }
    return len;
}

void config_stepper_print_act(uint8_t enable, uint32_t move_line) {
    struct stepper *s;
    uint8_t i;
    foreach_oid(i, s, command_config_stepper) {
        s->print_act = !!enable;
        s->move_line = move_line;
    }
}

// Set the stepper to stop on a "trigger event" (used in homing)
void
command_stepper_stop_on_trigger(uint32_t *args)
{
    struct stepper *s = stepper_oid_lookup(args[0]);
    struct trsync *ts = trsync_oid_lookup(args[1]);
    trsync_add_signal(ts, &s->stop_signal, stepper_stop);
}
DECL_COMMAND(command_stepper_stop_on_trigger,
             "stepper_stop_on_trigger oid=%c trsync_oid=%c");

void
stepper_shutdown(void)
{
    uint8_t i;
    struct stepper *s;
    foreach_oid(i, s, command_config_stepper) {
        move_queue_clear(&s->mq);
        stepper_stop(&s->stop_signal, 0);
    }
}
DECL_SHUTDOWN(stepper_shutdown);

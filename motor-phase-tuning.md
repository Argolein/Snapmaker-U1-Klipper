# Motor Phase Tuning — Feasibility And Implementation Plan

## Goal

Evaluate whether a Snapmaker U1 implementation of motor phase correction is
practical in this Klipper fork, using Prusa's phase-stepping work as a
reference model, but not as a direct port target.

Target outcome if feasible:
- reduce cogging-related print artifacts on X and Y
- calibrate once using the existing LIS2DW accelerometer path
- apply a deterministic runtime correction on the mainboard side

Important scope clarification:
- this is **not** a direct cherry-pick from Prusa
- this is **not** implementation-ready yet
- the first required step is a feasibility spike, not feature coding

---

## Verified Hardware Overview

| Component | Chip | Role |
|---|---|---|
| Mainboard MCU | **AT32F403A** | main realtime control, candidate runtime correction target |
| X/Y drivers | TMC2240 | possible direct-mode correction target |
| Z / extruder drivers | TMC2209 | out of scope |
| Toolboard MCU | STM32-based toolboard MCU | accelerometer transport |
| Accelerometer | LIS2DW on toolboard `e0` | vibration measurement |
| Host | Rockchip Linux host | Klipper host, capture orchestration, analysis |

Mainboard identification is already verified in
[at32f403a_config](/Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper/lava/at32f403a_config#L15)
and
[at32f403a_config](/Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper/lava/at32f403a_config#L49).
The earlier "probably STM32F4xx" wording was too vague.

### Verified `printer.cfg` facts

```ini
[mcu]
serial: /dev/ttyS6
baud: 460800

[resonance_tester]
accel_chip: lis2dw e0_lis2dw

[lis2dw e0_lis2dw]
cs_pin: e0:PA4
spi_bus: spi1
axes_map: y, x, z

[tmc2240 stepper_x]
cs_pin: PB12
spi_bus: spi2
run_current: 1.2

[tmc2240 stepper_y]
cs_pin: PE12
spi_bus: spi4
run_current: 1.2

[stepper_x]
dir_pin: !PC12
rotation_distance: 40
microsteps: 64

[stepper_y]
dir_pin: PB3
rotation_distance: 40
microsteps: 64
```

Implications:
- X and Y are on separate SPI buses
- only the two XY motor steppers are relevant for this feature
- the existing LIS2DW path is already wired for host-side vibration capture
- because the printer is CoreXY, `stepper_x` and `stepper_y` are the motor-level
  calibration targets, not independent cartesian axes

---

## What Is Actually Verified

| Topic | Status | Notes |
|---|---|---|
| X/Y-only scope | **verified** | TMC2240 on X/Y, TMC2209 elsewhere |
| LIS2DW bulk sampling reuse | **verified** | sensor capture path exists in `lis2dw.py` |
| `SHAPER_CALIBRATE`-style motion reuse | **not verified** | sensor backend reusable, motion path is not identical |
| Mainboard MCU identity | **verified** | AT32F403A, not an open question |
| TMC2240 direct-mode runtime path in this repo | **not verified** | current Klipper driver support is incomplete for this use |
| Prusa as conceptual reference | **verified** | useful for algorithm shape and calibration ideas |
| Prusa as direct implementation template | **false** | deeply tied to Prusa's Marlin motion stack |

---

## What Prusa Proves And What It Does Not

### Prusa proves
- additive phase correction is a real, shippable feature class
- storing harmonic spectra and expanding them into a runtime correction table is
  a sound design
- separate forward/backward correction data is reasonable
- calibration quality gates such as retry count, magnitude bounds, and
  prominence-based fitting are worth carrying over

### Prusa does not prove
- that the feature is small or easy to port into Klipper
- that U1 can skip driver phase resynchronization on enable/disable
- that TMC2240 in this repo already exposes the control path we need
- that the runtime can simply be added as one extra MCU timer loop

Concrete reasons:
- Prusa integrates phase stepping deeply into custom Marlin motion code and
  timer-driven step generation
- Prusa explicitly resynchronizes against `MSCNT`, switches to `256`
  microsteps, and only then enters direct mode
- the current U1 Klipper runtime is based on standard queued step events, not a
  dedicated phase-refresh loop

Relevant references:
- [phase_stepping.cpp](/Users/ArgoMac/GitHub-Development/Prusa-Firmware-Buddy/lib/Marlin/Marlin/src/feature/phase_stepping/phase_stepping.cpp#L400)
- [quick_tmc_spi.cpp](/Users/ArgoMac/GitHub-Development/Prusa-Firmware-Buddy/lib/Marlin/Marlin/src/feature/phase_stepping/quick_tmc_spi.cpp#L22)
- [lut.cpp](/Users/ArgoMac/GitHub-Development/Prusa-Firmware-Buddy/lib/Marlin/Marlin/src/feature/phase_stepping/lut.cpp#L76)

---

## Working Assumptions

| # | Item | Status |
|---|---|---|
| 1 | Scope remains X/Y only | **approved** |
| 2 | Use LIS2DW bulk capture infrastructure on the host | **approved** |
| 3 | Use additive phase-shift correction, inspired by Prusa | **proposed** |
| 4 | Use 1024 logical phase positions to match `MSCNT` range | **proposed** |
| 5 | Keep separate forward/backward correction data | **proposed** |
| 6 | Store harmonic data, not a raw 1024-entry table, in config | **proposed** |
| 7 | Runtime phase derived from step counters may be possible | **unverified** |
| 8 | Enable/disable may still require `MSCNT` resync logic | **likely** |
| 9 | XDirect sign/coils may differ by axis inversion | **likely, needs U1 validation** |
| 10 | MCU runtime architecture is the main risk | **verified** |

---

## Electrical Parameters

These calculations are still useful, but they are design inputs, not proof that
the runtime model is correct.

```text
rotation_distance       = 40 mm
full_steps_per_rotation = 200
electrical_cycle_length = rotation_distance / full_steps * 4
                        = 40 / 200 * 4
                        = 0.8 mm

runtime step_dist       = 0.003125 mm
steps_per_cycle         = electrical_cycle_length / step_dist
                        = 0.8 / 0.003125
                        = 256

MSCNT units per Klipper step = 1024 / 256
                             = 4

candidate phase formula: phase = (step_count * 4) % 1024
```

The earlier `16`-unit value was only a mistaken host-side formula. The current
prototype should derive phase math from the runtime step distance plus the known
electrical cycle geometry, not from a hard-coded microstep conversion shortcut.
Even with the corrected `4`-unit starting point, runtime synchronization across
enable, disable, pause, restarts, or mid-motion entry is still not proven.

---

## Current Repo Constraints

### Host-side capture
- The LIS2DW path already supports buffered bulk capture via
  [lis2dw.py](/Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper/klippy/extras/lis2dw.py#L85).
- `resonance_tester.py` already shows how to start/finish accelerometer capture
  around a motion sequence, see
  [resonance_tester.py](/Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper/klippy/extras/resonance_tester.py#L171).

What is reusable:
- sensor capture lifecycle
- host-side batching pattern
- G-code registration pattern

What is not reusable as-is:
- motion pattern
- FFT targets
- quality analysis
- result storage format

### Driver control
- `tmc2240.py` exposes `MSCNT` and the `direct_mode` bit, see
  [tmc2240.py](/Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper/klippy/extras/tmc2240.py#L26),
  [tmc2240.py](/Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper/klippy/extras/tmc2240.py#L45),
  [tmc2240.py](/Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper/klippy/extras/tmc2240.py#L123).
- `tmc2240.py` now also exposes the `DIRECT_MODE` register plus signed
  `coil_a` / `coil_b` fields for debug and prototype work.
- Unlike `tmc2130.py`, there is still no mature integrated runtime helper layer
  around these fields; the remaining work is no longer field exposure, but the
  actual runtime/current-update architecture.

This means the TMC2240 control path needs explicit design work before runtime
implementation can begin.

### MCU runtime
- **Jitter-Proof Executor (Verified)**: The repo now contains a dedicated MCU executor (`src/motor_phase_exec.c`) that uses a **Ring Buffer** to decouple the 9600Hz timer ISR from the blocking SPI task. This prevents phase jumps even during high host/main-loop load.
- **Precise Flash LUT (Verified)**: A 1024-point Sine table in MCU Flash provides zero-drift, high-precision baseline currents, replacing previous recursive math.
- **SPI Optimization**: Default SPI frequency for the executor is now **4MHz**, reducing bus occupancy per update.
- **Direct-mode polarity constraint (Verified)**: On U1, the host executor must fold in each motor's `dir_pin` inversion when computing `phase_advance`. Normal Step/Dir motion gets this from the MCU direction pin; XDIRECT does not, so omitting `get_dir_inverted()` makes the dual CoreXY baseline oppose itself even when timing and startup synchronization are otherwise correct.
- **Cross-motor idle phase observation (Reinterpreted)**: The paired CoreXY motors idle about 256 logical phase units apart in XDIRECT-space (`stepper_y` around `1021`, `stepper_x` around `769`), but that is an observation about two independent motors, not proof that carriage motion needs a baked-in cross-motor electrical phase offset. The safer default model is per-motor `MSCURACT`/`MSCNT` sync with no fixed partner offset; any partner phase offset should remain a debug override only.
- **Amplitude-ramp requirement (Verified on U1)**: Once timing, polarity, and partner phase are corrected, the next limiting factor is launch current. On Klipper/U1 the executor cannot rely on a tightly integrated motion pipeline to ease into direct-mode torque, so the MCU-side executor now needs an explicit coil-amplitude ramp in addition to the interval/frequency ramp; otherwise the first “real” aligned launch can trip `uv_cp` / `vm_uvlo`.
- **Runtime basis overrides (Added for U1 bring-up)**: `MOTOR_PHASE_EXEC_RUN` now accepts host-side partner overrides (`PARTNER_PHASE_OFFSET`, `PARTNER_SWAP_COILS`, `PARTNER_INVERT_A`, `PARTNER_INVERT_B`). This is deliberate Klipper-specific scaffolding: unlike Prusa's tighter firmware integration, U1 bring-up benefits from solving motor-basis alignment interactively without rebuilding firmware after every quarter-cycle or coil-map experiment.
- **Stable U1 baseline found**: The first crash-free visible-motion baseline on hardware is currently `PARTNER_PHASE_OFFSET=216`, `PARTNER_INVERT_B=1`, `COIL_SCALE=120`, `EXEC_IRUN_PCT=50`. The remaining end-of-move `thumb thumb` suggests the next improvement should be exit/stop shaping, not more startup basis hunting.
- **Stop/restore handshake requirement (Verified in code, pending hardware retest)**: a fixed host-side sleep after `mpe_stop()` is not reliable enough on U1. The host now waits for explicit MCU `MPE_IDLE` status before restoring `direct_mode`, `IHOLD_IRUN`, `intpol`, and `mres`; otherwise visible movement can still end in a late partner-motor GSTAT reset during teardown.
- **Safe-exit tail requirement (Implemented, pending hardware retest)**: the executor now decelerates through `MPE_ZERO_HOLD -> MPE_DRAIN -> MPE_IDLE`, and the host writes an explicit zero XDIRECT vector before disabling `direct_mode`. This is the first exit path that tries to guarantee both “zero current” and “queue drained” instead of just “timer stopped”.
- **Newest blocker location (Hardware-verified)**: the latest U1 log on the safe-exit build reached `starting`, `primed`, and `synchronized_start`, then shut down about two seconds later. That moves the active bottleneck back into the live launch/ramp/cruise law; teardown is no longer the first failing edge.
- **Newest active mitigation (Implemented, pending hardware retest)**: the MCU launch profile is now much softer: start interval multiplier `16`, slower interval ramp, slower amplitude ramp, and lower minimum start scale. This is the next principled Klipper-specific correction because the remaining fault now happens during live motion, not stop/restore.
- **Newest debug slice (Implemented, pending hardware retest)**: the executor now exposes live telemetry through `mpe_query`:
  - queue depth
  - max queue depth
  - overflow count
  - event count
  - transfer count
  - last transmitted phase
  - last transmitted scale
  and the host polls/logs these snapshots during `MOTOR_PHASE_EXEC_RUN`. This replaces further blind tuning with direct evidence about whether the remaining fault is scheduler/SPI backlog or pure electrical shutdown.
- **Telemetry verdict (Hardware-verified)**: the first telemetry run stayed healthy through ramp and into cruise:
  - `depth=0`
  - `max_depth=1`
  - `overflow_count=0`
  - `event_count ~= transfer_count`
  until shutdown. That rules out scheduler/SPI starvation as the main blocker and points to sustained electrical load in cruise.
- **Next control-law split (Implemented, pending hardware retest)**: `MOTOR_PHASE_EXEC_RUN` now accepts `PRIME_COIL_SCALE` so the launch/priming torque can stay high while the steady-state XDIRECT cruise amplitude (`COIL_SCALE`) is lowered.
- **Newest control-law split (Implemented, pending hardware retest)**: the executor path now exposes three amplitude regimes:
  - `PRIME_COIL_SCALE`
  - `BREAKAWAY_COIL_SCALE`
  - `COIL_SCALE`
  with a timed `BREAKAWAY_MS` window. This follows directly from the new U1 evidence:
  `48` is stable but stationary, while `64` can move but is not yet stable on longer runs.
- **Newest breakaway result (Hardware-verified)**: the first timed breakaway run
  with `COIL_SCALE=48`, `BREAKAWAY_COIL_SCALE=64`, `BREAKAWAY_MS=750`,
  `SPEED=0.5`, `DISTANCE=10` completed fully and cleanly, with healthy queue
  telemetry, but still produced no visible carriage motion. The stable cruise
  point is therefore established; the remaining gap is stronger short-term
  breakaway torque.
- **Newest MCU logic fix (Implemented, pending hardware retest)**: the first
  `BREAKAWAY_COIL_SCALE=80` run revealed that the executor still re-applied the
  generic upward amplitude ramp after entering `MPE_CRUISE`. That effectively
  canceled the intended `80 -> 48` taper and left the run pinned near `79/80`.
  The MCU executor now disables that upward ramp once it has entered cruise, so
  breakaway decay can actually settle to the lower stable cruise point.
- **Newest post-fix result (Hardware-verified)**: the corrected `80 -> 48`
  breakaway run now decays exactly as intended and completes cleanly, but still
  shows no visible carriage travel. This moves the active blocker away from the
  amplitude-decay logic and toward the underlying force/phase model.
- **Newest host debug-path fix (Implemented, pending hardware retest)**:
  `_monitor_executor_run()` now uses `reactor.pause()` at a 1.0 s cadence
  instead of `toolhead.dwell()` at 0.5 s. This should reduce Fluidd sluggishness
  and avoid polluting the host motion stack during executor-only runs.
- **Newest model reset (Implemented, pending hardware retest)**: the default
  `exec_partner_phase_offset` is reset to `0`. The executor should first prove
  carriage motion with each motor aligned to its own runtime electrical basis;
  fixed partner offsets are now treated as experiment knobs, not baseline truth.

---

## RAM / Timing Reality Check

The old estimate was too generic.

Verified U1 MCU configuration:
- clock: 240 MHz
- RAM: `0x18000` = 96 KB

Source:
- [at32f403a_config](/Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper/lava/at32f403a_config#L15)
- [at32f403a_config](/Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper/lava/at32f403a_config#L20)

Conclusion:
- the tentative LUT sizes are still plausible
- the old "192 KB / 168 MHz STM32F4xx" justification should not be reused
- timing comfort cannot be claimed until the actual SPI transaction path and ISR
  interaction are prototyped on U1 hardware

---

## Calibration Quality Inputs From Prusa

These are useful reference values, not U1-validated thresholds yet.

| Kriterium | Referenzwert |
|---|---|
| Minimale Magnitude | 0.008 |
| Maximale Magnitude | 0.4 |
| Verhältnis fwd/bck | 2.0 |
| Peak-Prominenz | 0.15 bis 0.2 |
| Retry count | 2 |

References:
- [calibration_config.hpp](/Users/ArgoMac/GitHub-Development/Prusa-Firmware-Buddy/lib/Marlin/Marlin/src/feature/phase_stepping/calibration_config.hpp#L20)
- [calibration.cpp](/Users/ArgoMac/GitHub-Development/Prusa-Firmware-Buddy/lib/Marlin/Marlin/src/feature/phase_stepping/calibration.cpp#L31)

---

## Recommended Implementation Plan

### Phase 0 — Feasibility Spike
- [ ] prove a host-side constant-velocity capture path on U1
- [ ] prove a TMC2240 control path for:
  - reading `MSCNT`
  - toggling direct mode
  - writing the required direct current register(s)
- [ ] determine whether runtime phase can be entered without desync
- [ ] measure whether SPI update latency is acceptable on real hardware

Acceptance checks:
- one controlled X or Y sweep can be captured repeatably
- one TMC2240 can be placed into and out of the required direct mode safely
- phase alignment strategy is documented from measured behavior, not assumed

Current implementation progress:
- repo now contains `klippy/extras/motor_phase_calibration.py`
- config now seeds:
  - `[force_move] enable_force_move: True`
  - `[motor_phase_calibration]`
- `MOTOR_PHASE_MEASURE` captures LIS2DW data during a controlled single-stepper
  move using `force_move`
- the current prototype now returns the motor to its start point after the
  capture move and reports:
  - runtime `step_dist`
  - `steps_per_cycle`
  - derived `mscnt_units_per_step`
  - effective `sample_rate`
  - `samples_per_cycle`
- `tmc2240.py` now exposes `DIRECT_MODE` register fields `coil_a` and `coil_b`
  so hardware experiments can use existing `SET_TMC_FIELD` / `DUMP_TMC`
  commands

Hardware validation completed so far:
- `MOTOR_PHASE_MEASURE` succeeded on real hardware for `stepper_x` and
  `stepper_y`
- `GCONF.direct_mode` toggling was validated on real hardware
- `DIRECT_MODE.coil_a` / `DIRECT_MODE.coil_b` read and write was validated on
  real hardware
- CSV output was verified under `/userdata/gcodes/motor_phase_data`

Observed capture performance on the U1 with the current prototype:
- `20 mm/s`: about `937` samples over `~2.018 s` => about `464 Hz`
- `40 mm/s`: about `575` samples over `~1.018 s` => about `565 Hz`
- `60 mm/s`: about `426` samples over `~0.685 s` => about `622 Hz`
- `80 mm/s`: about `370` samples over `~0.518 s` => about `714 Hz`

Validated reflashed return-path behavior:
- `stepper_x` at `40 mm/s`: `571` samples, `mscnt_units_per_step=4.000`,
  `returned_to_start=1`
- `stepper_y` at `40 mm/s`: `566` samples, `mscnt_units_per_step=4.000`,
  `returned_to_start=1`

Important measurement correction:
- the earlier `~556-561 Hz` figure was only a move-window sample density and
  therefore overstated the real CSV sampling rate
- host-side CSV analysis shows the actual timestamp-based rate is about
  `393-402 Hz`
- that yields only about `7.9-8.0` samples per electrical cycle at `40 mm/s`
- the command should therefore report timestamp-based sample rate separately
  from move-window sample density

Implications:
- the host-side capture path is viable for Phase 0
- sweep planning should use the real effective sample rate, not the nominal
  LIS2DW configuration rate
- `40 mm/s` is already below the current `10 samples/cycle` target
- the first bounded sweep range should therefore stay closer to roughly
  `25-32 mm/s`, not `40-80 mm/s`

Phase 0 commands available after flashing a build with this repo:
- `MOTOR_PHASE_MEASURE STEPPER=stepper_x SPEED=40 DISTANCE=40`
- `SET_TMC_FIELD STEPPER=stepper_x FIELD=direct_mode VALUE=1`
- `SET_TMC_FIELD STEPPER=stepper_x FIELD=coil_a VALUE=0`
- `SET_TMC_FIELD STEPPER=stepper_x FIELD=coil_b VALUE=0`
- `DUMP_TMC STEPPER=stepper_x REGISTER=DIRECT_MODE`

### Phase 1 — Host Calibration Prototype
- [ ] add a host-only prototype extra, tentatively
  `klippy/extras/motor_phase_calibration.py`
- [ ] implement constant-velocity sweep orchestration
- [ ] add safe automatic setup for repeatable runs:
  - ensure a known starting point
  - move to a safe center region before long sweeps
  - alternate direction or reposition to respect axis limits
- [ ] collect LIS2DW batches and export raw data
- [ ] implement offline or host-side harmonic extraction
- [ ] emit a report only; no MCU runtime integration yet

Current host-side analysis support:
- repo now contains
  [motor_phase_analyze.py](/Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper/scripts/motor_phase_analyze.py)
- it reads `MOTOR_PHASE_MEASURE` CSV files and reports:
  - effective sample rate from timestamps
  - electrical cycle and electrical frequency from the requested move speed
  - samples per electrical cycle
  - axis-wise harmonic magnitude and phase for the selected number of
    electrical harmonics
  - a simple recommended top speed for a target minimum samples-per-cycle ratio
  - optional forward/backward comparison grouped by direction-tagged filenames
- repo now also contains a step-generation-adjacent diagnostic command:
  - `MOTOR_PHASE_STEP_TRACE`
  - it runs a normal `force_move`-based motor move
  - it extracts real `stepcompress` history for the exact move window
  - it expands the compressed segments into per-step timestamps, MCU
    positions, commanded positions, and 0..1023 phase indices
  - it writes a CSV under the same `motor_phase_data` directory for later
    comparison with accelerometer captures

Example workflow with a file copied off the printer:
```bash
python3 scripts/motor_phase_analyze.py \
  /path/to/motor-phase-stepper_x-20260404_182850.csv \
  --distance-mm 40 \
  --speed-mm-s 40 \
  --rotation-distance 40 \
  --full-steps-per-rotation 200 \
  --harmonics 8
```

Forward/backward comparison workflow:
```bash
python3 scripts/motor_phase_analyze.py \
  /path/to/motor-phase-stepper_y-..._forward_28p0.csv \
  /path/to/motor-phase-stepper_y-..._backward_28p0.csv \
  --distance-mm 40 \
  --speed-mm-s 28 \
  --rotation-distance 40 \
  --full-steps-per-rotation 200 \
  --harmonics 8 \
  --compare-fb
```

Repeated-run aggregation workflow:
```bash
python3 scripts/motor_phase_analyze.py \
  /path/to/motor-phase-stepper_y-..._forward_30p0.csv \
  /path/to/motor-phase-stepper_y-..._backward_30p0.csv \
  /path/to/motor-phase-stepper_y-..._forward_30p0.csv \
  /path/to/motor-phase-stepper_y-..._backward_30p0.csv \
  --distance-mm 40 \
  --speed-mm-s 30 \
  --rotation-distance 40 \
  --full-steps-per-rotation 200 \
  --harmonics 8 \
  --aggregate-fb
```

First small-basis export from aggregated runs:
```bash
python3 scripts/motor_phase_analyze.py \
  /path/to/motor-phase-stepper_y-..._forward_30p0.csv \
  /path/to/motor-phase-stepper_y-..._backward_30p0.csv \
  /path/to/motor-phase-stepper_y-..._forward_30p0.csv \
  /path/to/motor-phase-stepper_y-..._backward_30p0.csv \
  --distance-mm 40 \
  --speed-mm-s 30 \
  --rotation-distance 40 \
  --full-steps-per-rotation 200 \
  --harmonics 8 \
  --aggregate-fb \
  --export-basis
```

First normalized fit/LUT export from the aggregated basis:
```bash
python3 scripts/motor_phase_analyze.py \
  /path/to/motor-phase-stepper_y-..._forward_30p0.csv \
  /path/to/motor-phase-stepper_y-..._backward_30p0.csv \
  /path/to/motor-phase-stepper_y-..._forward_30p0.csv \
  /path/to/motor-phase-stepper_y-..._backward_30p0.csv \
  --distance-mm 40 \
  --speed-mm-s 30 \
  --rotation-distance 40 \
  --full-steps-per-rotation 200 \
  --harmonics 8 \
  --aggregate-fb \
  --export-basis \
  --export-fit
```

Runtime-oriented prototype payload export:
```bash
python3 scripts/motor_phase_analyze.py \
  /path/to/motor-phase-stepper_y-..._forward_30p0.csv \
  /path/to/motor-phase-stepper_y-..._backward_30p0.csv \
  /path/to/motor-phase-stepper_y-..._forward_30p0.csv \
  /path/to/motor-phase-stepper_y-..._backward_30p0.csv \
  --distance-mm 40 \
  --speed-mm-s 30 \
  --rotation-distance 40 \
  --full-steps-per-rotation 200 \
  --harmonics 8 \
  --aggregate-fb \
  --export-basis \
  --export-fit \
  --export-runtime-payload
```

Current first printer-side consumer:
- `MOTOR_PHASE_DIRECT_SAMPLE`
- `MOTOR_PHASE_DIRECT_SCAN`
- `MOTOR_PHASE_LOAD_PAYLOAD`
- `MOTOR_PHASE_SHOW_PAYLOAD`

The intended workflow is:
1. generate analyzer JSON with `--export-runtime-payload --json`
2. copy that JSON to the printer
3. optionally load it once into a named profile
4. run a low-amplitude direct-mode sample or scan against one XY stepper

Example payload export:
```bash
python3 scripts/motor_phase_analyze.py \
  tmp/motor_phase_data/*30p0.csv \
  --speed-mm-s 30 \
  --distance-mm 40 \
  --rotation-distance 40 \
  --full-steps-per-rotation 200 \
  --harmonics 8 \
  --aggregate-fb \
  --export-basis \
  --export-fit \
  --export-runtime-payload \
  --json > motor_phase_runtime_payload_30.json
```

Example first safe printer-side sample:
```gcode
MOTOR_PHASE_LOAD_PAYLOAD PROFILE=y30 PAYLOAD=/home/lava/printer_data/config/motor_phase_runtime_payload_30.json SPEED_MM_S=30
MOTOR_PHASE_SHOW_PAYLOAD PROFILE=y30
MOTOR_PHASE_DIRECT_SAMPLE \
  STEPPER=stepper_y \
  PROFILE=y30 \
  DIRECTION=forward \
  PHASE_INDEX=0 \
  SHIFT_SCALE_DEG=10 \
  COIL_SCALE=120 \
  DWELL=0.2
```

Example first slow scan:
```gcode
MOTOR_PHASE_DIRECT_SCAN \
  STEPPER=stepper_y \
  PROFILE=y30 \
  DIRECTION=forward \
  START=0 \
  COUNT=16 \
  STRIDE=8 \
  SHIFT_SCALE_DEG=10 \
  COIL_SCALE=120 \
  DWELL=0.05
```

Current in-printer sweep support:
- `MOTOR_PHASE_SWEEP` now exists in
  [motor_phase_calibration.py](/Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper/klippy/extras/motor_phase_calibration.py)
- it can:
  - home `X/Y` only when needed
  - move to a safe staging point
  - validate that the requested single-stepper excursion stays within XY bounds
  - run a bounded list of `MOTOR_PHASE_MEASURE` captures in sequence
  - optionally run both forward and backward sweeps in one command

Example bounded sweep:
```gcode
MOTOR_PHASE_SWEEP STEPPER=stepper_x SPEEDS=25,28,30,32 DISTANCE=40
```

Example bidirectional bounded sweep:
```gcode
MOTOR_PHASE_SWEEP STEPPER=stepper_y SPEEDS=25,28,30 DIRECTION=both DISTANCE=40
```

Current safety model:
- supports `stepper_x` and `stepper_y`
- assumes CoreXY kinematics and validates the projected cartesian endpoint of
  the one-motor excursion
- homes `X/Y` automatically if needed, but does not re-home when they are
  already homed
- always stages to the sweep center before running
- uses the XY center by default, with optional `CENTER_X` / `CENTER_Y`
- raises to `SAFE_Z` before XY staging if Z is already homed

Current bounded-sweep result:
- real copied `stepper_y` sweep files now show:
  - `25 mm/s`: about `394.7 Hz`, about `12.63` samples/electrical-cycle
  - `28 mm/s`: about `397.4 Hz`, about `11.35` samples/electrical-cycle
  - `30 mm/s`: about `397.5 Hz`, about `10.60` samples/electrical-cycle
  - `32 mm/s`: about `396.5 Hz`, about `9.91` samples/electrical-cycle
- implication:
  - the real sample rate is effectively flat around `395-397 Hz`
  - `25-30 mm/s` all satisfy the current `10 samples/cycle` target
  - `32 mm/s` is already borderline
  - the first practical sweep envelope should therefore be `25-30 mm/s`, with
    `32 mm/s` kept as an optional upper probe instead of a default working
    speed
  - `28-30 mm/s` is the best current compromise between usable electrical
    frequency and still-safe sample density
- repeated `30 mm/s` forward/backward runs now aggregate to:
  - about `395.6 Hz` forward and `397.1 Hz` backward sample rate
  - about `10.55` forward and `10.59` backward samples/electrical-cycle
  - aggregate `H1` magnitude ratio about `1.081`
  - aggregate `H1` phase delta about `27.9°`
- practical consequence:
  - `30 mm/s` is the best current first working point
  - the first correction fit should start small, likely around `H1-H3` or
    `H1-H4`, instead of immediately trying a wide high-order fit
- the current basis exporter now confirms the first conservative small basis:
  - with the current ratio threshold `2.5`, `H1-H3` are stable enough to keep
  - `H4` is currently rejected because its aggregate forward/backward magnitude
    ratio is already about `2.651`
- the current fit exporter now turns that basis into:
  - a first normalized forward fit from aggregated `H1-H3`
  - a first normalized backward fit from aggregated `H1-H3`
  - a 1024-point LUT preview for both directions
- the current runtime payload exporter now turns that fit into:
  - a forward `phase_offset_q15[1024]` prototype table
  - a backward `phase_offset_q15[1024]` prototype table
  - an explicit forward `prototype_direct_profile` with
    `coil_a_unit_q15[1024]` / `coil_b_unit_q15[1024]`
  - an explicit backward `prototype_direct_profile` with
    `coil_a_unit_q15[1024]` / `coil_b_unit_q15[1024]`
  - the current prototype direct profile uses a pinned
    `shift_scale_deg_default=10.0`
  - explicit metadata that this is a U1 runtime target representation, but
    still only in accelerometer-domain normalized form
- the current printer-side consumer now reads that payload and can:
  - apply one sample at a chosen phase index
  - apply a slow controlled scan over multiple phase indices
  - automatically enter and exit `direct_mode`
  - prefer the explicit direct coil profile when the requested
    `SHIFT_SCALE_DEG` matches the exported prototype default
  - fall back to the older `phase_offset_q15` conversion path when needed
- first hardware validation now exists for that consumer:
  - `MOTOR_PHASE_DIRECT_SAMPLE` completed successfully on `stepper_y`
  - `MOTOR_PHASE_DIRECT_SCAN` completed successfully on `stepper_y`
  - the payload was parsed and converted into low-amplitude direct-mode coil writes
  - the newer profile-based path is now also validated on hardware:
    - `MOTOR_PHASE_LOAD_PAYLOAD` loads the runtime payload into memory
    - `MOTOR_PHASE_SHOW_PAYLOAD` reports the expected metadata
    - `MOTOR_PHASE_DIRECT_SAMPLE` / `MOTOR_PHASE_DIRECT_SCAN` also work through the loaded profile path
    - observed behavior stayed mild, with only slight `klong` noises
- the newer explicit direct-profile path is now also validated on hardware:
  - the refreshed runtime payload exposes `direct_profile=1`
  - `MOTOR_PHASE_DIRECT_SAMPLE` now runs with `representation=prototype_direct_profile`
  - `MOTOR_PHASE_DIRECT_SCAN` now runs with `representation=prototype_direct_profile`
- the current printer-side prototype now also has a managed persistent profile path:
  - `MOTOR_PHASE_STORE_PAYLOAD` imports one analyzer payload item into a managed
    persistent profile file under `/home/lava/printer_data/config/motor_phase_profiles`
  - `MOTOR_PHASE_LOAD_PROFILE` reloads such a stored profile into memory
  - `MOTOR_PHASE_LIST_PROFILES` reports which stored profiles exist and whether
    they are already loaded
- the next small persistence step is now implemented:
  - `[motor_phase_calibration]` can declare `autoload_profiles: ...`
  - autoload is explicit opt-in per profile name; it does not implicitly load
    every file in the profile directory
  - `MOTOR_PHASE_LIST_PROFILES` now also reports `autoload=0/1`
- the first minimal effectiveness-test path is now implemented:
  - `MOTOR_PHASE_DIRECT_MEASURE` runs an open-loop host-driven direct-mode move
    with automatic homing/centering, LIS2DW capture, and return-to-start
  - `VARIANT=baseline` uses an ideal direct sine/cosine table
  - `VARIANT=profile` uses the loaded stored motor-phase profile
  - this is explicitly an A/B feasibility tool, not yet the final runtime path
- real hardware feedback on that path is negative for benefit validation:
  - the motion sounds like coarse stepwise stuttering, not like a realistic
    smooth low-speed move
  - that matches the implementation: host-driven SPI coil writes paced by
    `toolhead.dwell()` are too coarse to evaluate quieter motion or VFA
  - therefore this path should be kept only as a low-level direct-mode
    feasibility probe, not as the proof path for feature usefulness
- current repo validation also suggests why:
  - `angle.py` exposes calibration/sync helpers and the TMC layer tracks
    phase offsets, but there is no credible host-side motion-synchronous hook
    here for per-phase current updates during real movement
- the next meaningful prototype has to move closer to step generation /
  MCU timing instead of adding more host-driven direct-mode loops
- in the current Klipper stack, the most plausible integration points now
  look like:
  - host-side near `itersolve_generate_steps()` / `stepcompress`
  - or MCU-side near `queue_step` / `stepper_event`
  - not another G-Code layer that paces `direct_mode` updates with
    `toolhead.dwell()`
- the first practical step in that direction is now in-repo:
  - `MOTOR_PHASE_STEP_TRACE` does not attempt correction
  - instead, it proves whether we can recover the real generated step cadence
    and phase progression from the normal motion path
  - that trace data is the minimal prerequisite for any later
    step-generation-coupled or MCU-coupled correction experiment
  - first hardware use immediately found two implementation mistakes:
    - the command originally skipped the safe homing/centering rules used by
      the sweep path
    - the first trace export kept unstable references to `dump_steps()` data
      instead of copying stable primitive values first
  - both issues are now corrected in the repo:
    - `MOTOR_PHASE_STEP_TRACE` now auto-homes XY if needed and always stages to
      a validated center position before tracing
    - the trace path now copies, sorts, and move-window-filters the extracted
      step history before writing the CSV
  - the next practical bridge is now also in-repo:
    - `MOTOR_PHASE_CAPTURE_SYNC`
    - it performs one safe staged real move
    - it writes both the LIS2DW capture CSV and the expanded step/phase trace
      CSV for that same move
    - this is the first command that can align accelerometer response against
      reconstructed electrical phase from the normal motion path
  - first synchronized hardware run is now validated:
    - for `stepper_y`, `30 mm/s`, `20 mm`, the trace side recorded `6400`
      steps over `0.6665625 s`, exactly `9600 Hz`
    - that matches the expected normal-motion step rate from
      `30 mm/s / 0.003125 mm`
    - every traced step advanced `mcu_position` by `+1` and `phase_index` by
      `+4`, exactly as expected from the current electrical geometry
    - the accel and trace files overlap cleanly over the full move window, with
      only tens of microseconds between an accel timestamp and its nearest trace
      sample
  - implication:
    - the repo can now reconstruct electrical phase on the normal motion path
      accurately enough to start phase-binned residual analysis offline
- that managed persistent profile path is now also validated on hardware:
  - storing `y30` creates `/home/lava/printer_data/config/motor_phase_profiles/y30.json`
  - listing reports the stored profile and its loaded state
  - reloading from the stored profile preserves the `direct_profile=1` path
- important limitation:
  - this is still an accelerometer-domain prototype shape, not yet a validated
    direct-mode current table

Acceptance checks:
- repeated runs produce stable peaks at the expected electrical harmonics
- basic quality metrics can flag obviously bad runs

### Phase 2 — Driver Control & Executor Hardening (Completed)
- [x] Implement a dedicated MCU task for TMC2240 `DIRECT_MODE` updates.
- [x] **Ring Buffer Integration**: MCU side now buffers phase updates to survive task jitter.
- [x] **Flash Sine LUT**: 1024-point table for baseline motion (full cycle, negative half fixed 2026-04-07).
- [x] **GSTAT Safety Gate**: Host-side abort if driver is in undervoltage/reset before start.
- [x] **Sync Hardening**: Minimized latency between `MSCNT` capture and `DIRECT_MODE` activation.

### Phase 3 — Baseline Validation (Completed)
- [x] Verify stable motor movement in `DIRECT_MODE` using the jitter-proof executor.
- [x] Identify mechanical harmonics (H1, H2, H3) using the T0 accelerometer.
- [x] Confirm clean exit from `DIRECT_MODE` back to normal step/dir with auto-rehoming.

### Phase 4 — Runtime Correction & VFA Evaluation (In Progress)
- [x] **MCU Correction Engine**: Implemented additive correction LUTs (`corr_a`, `corr_b`) in the MCU task.
- [x] **Host Upload Logic**: Implemented chunked upload of harmonic profiles from Python to MCU.
- [x] **Stable Measurement Path**: Integrated LIS2DW sensing into the high-rate `EXEC_RUN` command.
- [x] **Code quality fixes** (2026-04-07): see bug log below.
- [ ] Rebuild MCU firmware and flash to printer.
- [ ] Retest baseline executor with corrected full sine table.
- [ ] Verify RMS vibration reduction with active phase correction vs. baseline.
- [ ] Implement full interpolated LUT correction for production use.

#### Bug log — fixed 2026-04-07

**BUG 1 — Sine table incomplete (critical, would prevent correct motor drive)**
- `sine_table[1024]` in `src/stm32/motor_phase_exec.c` was only initialized with 511 entries.
- Indices 511–1023 (the negative half, 180°–360°) were zero instead of the expected negative sine values.
- Effect: coil waveform was half-wave rectified; coil_b was zero for phase indices 256–767.
- Fix: added `sine_table[511]=100` and entries 512–1023 as the exact negative mirror (`sine_table[512+k] = -sine_table[k]`).
- Table is now 1024 entries; peak at index 255 = 16384, trough at index 767 = -16384.

**BUG 2 — DECL_COMMAND macros in wrong file (architectural, caused "Unknown command: config_mpe")**
- `DECL_COMMAND` was added in `src/command.c` via proxy functions (`command_mpe_config_proxy`, etc.) instead of directly in `motor_phase_exec.c`.
- This was the direct cause of the "Unknown command: config_mpe" error on any build where `command.c` changes were not flashed.
- Fix: moved `DECL_COMMAND` macros directly into `src/stm32/motor_phase_exec.c` after each function; removed the proxy block from `command.c`.
- Actual command protocol (host → MCU):
  - `config_mpe oid=%c spi_oid=%c`
  - `mpe_start oid=%c interval=%u phase_index=%u coil_scale=%c phase_advance=%c`
  - `mpe_stop oid=%c`
  - `mpe_update oid=%c offset=%u data=%*s table=%c`

**BUG 3 — Unnecessary linker hack (architectural)**
- `mpe_setup_dummy()` (empty function) was called from `at32f403a_clock_setup()` to force linker inclusion.
- Not needed: `src/stm32/Makefile` already includes `stm32/motor_phase_exec.c` unconditionally for `CONFIG_MACH_AT32F403A`.
- Fix: removed `extern void mpe_setup_dummy(void)` and the call from `at32f403a.c`; removed the empty function and `DECL_INIT(mpe_setup_dummy)` from `motor_phase_exec.c`.

**BUG 4 — Wrong mres value in driver restore (would leave motor in incorrect microstep mode)**
- `_restore_executor_driver()` in `klippy/extras/motor_phase_calibration.py` set `mres=4` (16 microsteps).
- The U1 printer.cfg configures `microsteps: 64` which maps to `mres=2`.
- Effect: after `MOTOR_PHASE_EXEC_RUN`, the TMC driver was left in 16-microstep mode. Subsequent moves (including `G28 X Y`) would advance the rotor 4× farther per step than expected — dangerous.
- Fix: changed `mres=4` → `mres=2` in `_restore_executor_driver()`.

**BUG 5 — MOTOR_PHASE_LOAD_PAYLOAD parsed inline JSON instead of file path**
- The command took the PAYLOAD parameter as raw JSON text, which fails for any real payload file (G-code parameter limit applies; real payload JSON is 10s of KB).
- The documented usage (`PAYLOAD=/path/to/file.json`) was broken.
- Fix: `cmd_MOTOR_PHASE_LOAD_PAYLOAD` now opens and reads the file at the given path.

**BUG 6 — Wasted `get_register()` call in `_set_tmc_field()` (minor inefficiency)**
- `reg_val = driver.mcu_tmc.get_register(reg_name)` was called but then immediately overwritten by `reg_val = driver.fields.set_field(field_name, value)`.
- The `get_register` result was never used.
- Fix: removed the unused `get_register` call.

#### Verified MCU-side architecture (from source exploration 2026-04-05, corrected 2026-04-07)

#### TMC2240 DIRECT_MODE register (0x2D)
- `coil_a`: bits [8:0], **9-bit signed**, range −255..+255
- `coil_b`: bits [24:16], **9-bit signed**, range −255..+255
- SPI write: 5 bytes total
  - byte 0: `0xAD` (write-bit 0x80 | register 0x2D)
  - bytes 1–4: 32-bit big-endian data
  - packing: `val = ((coil_b & 0x1FF) << 16) | (coil_a & 0x1FF)`

#### ISR / SPI constraint
- `spidev_transfer()` is **blocking** — cannot be called from a timer ISR
- Only pattern that works:

```
Timer ISR (104 µs period)  →  set pending flag, sched_wake_task()
Main-loop task              →  check wake, call spidev_transfer() [~5-40 µs]
```

#### Baseline table: no upload required
- The ideal baseline (cos/sin) is a compile-time constant
- `sine_table[1024]` in MCU flash covers the full electrical cycle (indices 0–1023)
- `coil_a` uses `sine_table[pi]`, `coil_b` uses `sine_table[(pi+256)&1023]` (90° quadrature)
- Host does not need to send anything for baseline mode

#### MCU module: `src/stm32/motor_phase_exec.c`
- `config_mpe oid=%c spi_oid=%c` — registered per executor OID, not as one global singleton anymore
- `mpe_start oid=%c interval=%u phase_index=%u coil_scale=%c phase_advance=%hi`
- `mpe_stop oid=%c`
- `mpe_update oid=%c offset=%u data=%*s table=%c`
- Embedded `struct timer` fires at phase-update rate, wakes a `DECL_TASK`
- Task iterates active executor OIDs and writes one 5-byte DIRECT_MODE SPI
  frame per buffered tick
- Build integration: `src-$(CONFIG_HAVE_GPIO_SPI) += motor_phase_exec.c`

#### U1-specific CoreXY implication
- A one-motor xdirect executor is not enough for visible XY motion on U1.
- U1 is CoreXY, so coherent carriage motion needs both motors in direct mode:
  - pure `Y` motion => `stepper_x` and `stepper_y` run with opposite phase
    advance signs
  - pure `X` motion => both run with the same sign
- The host path now stages two executor channels:
  - target motor can carry a correction table
  - partner motor currently runs baseline only
- The host now primes both motors in lockstep before the periodic executor
  starts, instead of priming them one after the other.
- The MCU executor now applies a simple startup ramp by beginning with a slower
  `interval_current` and converging toward `interval_target`.
- A concrete motion-law bug was found in this path:
  `phase_advance` arrived from the host as signed `%hi`, but the MCU stored it
  in a `uint8_t`. That silently turned `-1` into `255`, which explains the
  observed buzzing/scratching instead of coherent CoreXY travel.
- A second deterministic launch issue remains possible even with correct signs:
  both motors must share one absolute MCU `start_clock`, otherwise they can
  begin on slightly different MCU times and still buzz. The executor command
  now carries `start_clock=%u`, and the MCU executor uses explicit
  `idle/armed/ramp/cruise` states.

#### Timing budget at 30 mm/s
- Phase update rate: 30 mm/s ÷ 0.003125 mm/step = **9600 Hz** → period **104 µs**
- Hardware SPI at 1 MHz: ~40 µs per write (38% duty)
- Hardware SPI at 4 MHz: ~10 µs per write (10% duty)
- Task scheduling jitter must stay well below 104 µs to avoid phase stuttering

Acceptance checks for the dedicated executor:
- `baseline_direct_profile` produces audibly smooth motion instead of
  stuttering
- repeated baseline runs remain phase-stable and do not leave the axis
  desynchronized
- `correction_direct_profile` can be switched on and off without changing the
  basic motion smoothness envelope
- only after the baseline path is smooth should any `baseline` vs `correction`
  A/B comparison be treated as meaningful

Abort / rollback gates:
- stop the dedicated executor track if the first baseline-only executor still
  audibly steps or clicks like the rejected scheduled-SPI prototype
- stop and re-evaluate if direct-mode exit cannot reliably return control to
  normal step/dir motion
- do not proceed to print-quality or VFA testing until the baseline-only
  executor is smooth enough to be a credible comparison partner

Acceptance checks:
- one axis runs a correction table without missed steps or driver desync
- no regressions in normal motion when the feature is disabled

### Phase 4 — Integrated Feature
- [ ] add config storage format
- [ ] add enable/disable and status commands
- [ ] add boot-time load path
- [ ] add post-calibration verification run

Acceptance checks:
- calibration can be run end-to-end on hardware
- correction persists across reboot
- measured vibration reduction is reproducible

---

## Open Questions

1. Which exact TMC2240 register path should be used for the U1 direct-current
   write path in this Klipper fork?
2. Can the feature safely derive runtime phase from step counters alone after an
   initial sync, or is periodic/explicit `MSCNT` resync required?
3. What is the smallest U1-safe enable/disable sequence for direct mode?
4. Should the first usable version store results in `printer.cfg`, an auxiliary
   generated file, or both?

---

## References

- U1 repo:
  - [printer.cfg](/Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper/lava/printer.cfg)
  - [at32f403a_config](/Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper/lava/at32f403a_config)
  - [lis2dw.py](/Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper/klippy/extras/lis2dw.py)
  - [resonance_tester.py](/Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper/klippy/extras/resonance_tester.py)
  - [tmc2240.py](/Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper/klippy/extras/tmc2240.py)
  - [stepper.c](/Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper/src/stepper.c)
- Prusa repo:
  - [phase_stepping.cpp](/Users/ArgoMac/GitHub-Development/Prusa-Firmware-Buddy/lib/Marlin/Marlin/src/feature/phase_stepping/phase_stepping.cpp)
  - [quick_tmc_spi.cpp](/Users/ArgoMac/GitHub-Development/Prusa-Firmware-Buddy/lib/Marlin/Marlin/src/feature/phase_stepping/quick_tmc_spi.cpp)
  - [lut.cpp](/Users/ArgoMac/GitHub-Development/Prusa-Firmware-Buddy/lib/Marlin/Marlin/src/feature/phase_stepping/lut.cpp)
  - [calibration.cpp](/Users/ArgoMac/GitHub-Development/Prusa-Firmware-Buddy/lib/Marlin/Marlin/src/feature/phase_stepping/calibration.cpp)
  - [calibration_config.hpp](/Users/ArgoMac/GitHub-Development/Prusa-Firmware-Buddy/lib/Marlin/Marlin/src/feature/phase_stepping/calibration_config.hpp)
  - [M97x.cpp](/Users/ArgoMac/GitHub-Development/Prusa-Firmware-Buddy/lib/Marlin/Marlin/src/gcode/feature/phase_stepping/M97x.cpp)
  - [phase_stepping.py](/Users/ArgoMac/GitHub-Development/Prusa-Firmware-Buddy/utils/phase_stepping/phase_stepping.py)

---

## Handoff

- Agent: Codex
- Date: 2026-04-08
- Completed this session:
  - confirmed on hardware that the softer-launch build can produce visible
    motion, but the best-known baseline still ends in shutdown
  - concluded that further progress now requires executor telemetry instead of
    more blind parameter sweeps
  - extended MCU `mpe_query` to report queue/cadence telemetry:
    - depth
    - max depth
    - overflow count
    - event count
    - transfer count
    - last sent phase
    - last sent scale
  - updated `MOTOR_PHASE_EXEC_RUN` so the host logs `run[n]` executor snapshots
    while the move is in progress
  - interpreted the first telemetry run and confirmed the counters stay healthy
    into cruise, which rules out queue starvation and points to electrical
    cruise load
  - added `PRIME_COIL_SCALE` so launch torque and cruise amplitude can now be
    tuned separately without touching the MCU protocol
  - confirmed on hardware that:
    - `64 @ 0.5 mm/s, 5 mm` is stable
    - `64 @ 1.0 mm/s, 10 mm` still crashes in cruise
    - `48 @ 0.5..1.0 mm/s` is stable but produces no visible carriage motion
  - implemented `BREAKAWAY_COIL_SCALE` and `BREAKAWAY_MS` so the post-start
    motion can stay briefly above the stable cruise current before tapering
  - validated the first breakaway retest on hardware:
    clean completion, no crash, but still no visible movement
  - rebuilt fresh SoC + main MCU artifacts for this telemetry slice
- Stopped at:
  - the first `BREAKAWAY_COIL_SCALE=80` retest exposed a concrete MCU executor
    bug instead of a pure torque limit
  - the bug is fixed locally in `src/motor_phase_exec.c`, rebuilt, and ready
    for hardware retest
  - the next run must validate that breakaway now really decays into the
    stable cruise point
- Next step:
  1. Flash the rebuilt MCU + SoC images with the fixed breakaway decay:
     ```bash
     scp /Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper/out_at32f403a/at32f403a.bin root@192.168.178.95:/tmp/
     ssh root@192.168.178.95 /home/lava/bin/systemUpgrade.sh upgrade mcu0 /tmp/at32f403a.bin
     ```
     ```bash
     scp /Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper/tmp/firmware/update.img root@192.168.178.95:/tmp/
     ssh root@192.168.178.95 /home/lava/bin/systemUpgrade.sh upgrade soc /tmp/update.img
     ssh root@192.168.178.95 reboot
     ```
  2. Retest the exact `80 -> 48` case that previously stayed pinned near full
     breakaway:
     ```gcode
     MOTOR_PHASE_EXEC_RUN STEPPER=stepper_y CARRIAGE_AXIS=Y DIRECTION=forward SPEED=0.5 DISTANCE=10 COIL_SCALE=48 PRIME_COIL_SCALE=120 BREAKAWAY_COIL_SCALE=80 BREAKAWAY_MS=500 PHASE_STRIDE=1 WRITE_CSV=0 PARTNER_PHASE_OFFSET=216 PARTNER_INVERT_B=1 EXEC_IRUN_PCT=40
     ```
  3. Inspect the resulting logs:
     - inspect `motor_phase_exec_run: run[n] ...`
     - confirm counters remain healthy
     - verify that the scale now actually drops toward `48`
     - check whether that corrected control law finally produces visible
       carriage travel
  4. If still stable but stationary after the decay fix, revisit partner basis
     / phase-model assumptions instead of pushing current upward again
- Open blockers:
  - none at the source-code level
  - the remaining blocker is hardware validation of the fixed cruise-decay
    behavior
- Decisions made this session:
  - the next useful debug primitive is executor telemetry, not another broad
    parameter sweep
  - at the current low-speed bring-up point, host polling during the run is an
    acceptable debug aid because the real phase cadence still lives entirely on
    the MCU
  - since the first telemetry run showed healthy queueing into cruise, the next
    useful control-law change is to split launch torque from cruise amplitude
  - the stable U1 direct-mode window now appears to require at least three
    regimes: prime, breakaway, and cruise
  - the first `BREAKAWAY_COIL_SCALE=80` run did not test the intended control
    law, because a real MCU bug kept re-raising amplitude during cruise; fix
    that before drawing further conclusions about torque sufficiency

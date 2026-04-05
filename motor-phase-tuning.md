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
- The U1 MCU runtime currently follows the standard Klipper queued-step model in
  [stepper.c](/Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper/src/stepper.c#L68).
- There is no existing U1-local phase-refresh subsystem, no per-axis direct-mode
  current writer, and no obvious MCU SPI service dedicated to this feature.

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

### Phase 2 — Driver Control Prototype
- [ ] extend the U1 TMC2240 path with the exact register support required for
  direct-mode experiments
- [ ] validate axis inversion, coil ordering, and enable/disable sequencing on U1
- [ ] verify whether Prusa's coil swap and inversion rule matches U1 behavior

Acceptance checks:
- direct-mode entry and exit works without leaving the axis desynchronized
- X and Y sign conventions are validated on hardware

### Phase 3 — MCU Runtime Architecture
- [ ] design the smallest possible MCU runtime integration
- [x] reject a naive timed `direct_mode` overlay on top of normal step/dir
  motion as the runtime path
- [x] reject pre-scheduled SPI current writes as the runtime path
- [x] define the smallest dedicated executor that owns current updates for one
  motor without relying on host pacing
- [ ] prototype one axis only before generalizing

Minimal dedicated executor plan:
- scope the first runtime slice to `stepper_y @ 30 mm/s` only, with the current
  frozen `H2/H4` residual working set as the only supported correction basis
- place the first executor at the mainboard MCU side near the actual executed
  step path, not at the host `generate_steps()` seam and not at the queue/load
  seam
- keep the first slice isolated from normal cartesian motion logic:
  - a dedicated enable/disable path
  - a dedicated direct-current update path
  - a dedicated stop/rollback path back to normal step/dir
- require the executor to support two modes before any benefit claim:
  - `baseline_direct_profile`: smooth ideal sinus/cosinus current playback
  - `correction_direct_profile`: the same baseline plus the frozen `H2/H4`
    phase-offset profile
- keep the first prototype intentionally narrow:
  - one motor only
  - one speed only
  - one fixed current scale only
  - no persistence or UI integration
- precompute correction data on the host and feed the MCU a compact working set
  instead of trying to derive harmonics inside the MCU
- treat normal motion compatibility as an explicit gate:
  - the axis must always be able to exit direct mode cleanly
  - a failed run must not leave the printer desynchronized for the next normal
    move

Verified MCU-side architecture (from source exploration 2026-04-05):

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
- Store as `const int16_t baseline_coil_a[1024]` / `_coil_b[1024]` in MCU flash
- Host does not need to send anything for baseline mode

#### New MCU module: `src/motor_phase_exec.c`
- New `config_motor_phase_exec` command: `oid=%c spi_oid=%c cs_pin=%u interval=%u`
  - `spi_oid`: references an already-configured `spidev_s` object (own OID, not shared)
  - `interval`: timer interval in MCU clock ticks for the desired phase-update rate
- `motor_phase_exec_start` / `motor_phase_exec_stop` commands orchestrated from host
- Embedded `struct timer` fires at phase-update rate, wakes a `DECL_TASK`
- Task writes 5-byte DIRECT_MODE SPI frame per tick
- Build integration: `src-$(CONFIG_HAVE_GPIO_SPI) += motor_phase_exec.c`

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
- Date: 2026-04-05
- Completed this session:
  - reviewed the newly added baseline-only executor implementation and host
    integration after the previous session
  - corrected stale technical statements in this document:
    - `tmc2240.py` already exposes `DIRECT_MODE`, `coil_a`, and `coil_b`
    - the current build hook is `src-$(CONFIG_HAVE_GPIO_SPI) += motor_phase_exec.c`
  - aligned the documented SoC/MCU build and flash workflow with the actual repo
    state
  - rechecked the current executor architecture against the code:
    - timer ISR advances phase and sets a single pending bit
    - `DECL_TASK` drains that bit and performs the blocking SPI write
    - this is the main risk if service falls behind
  - built fresh host and MCU artifacts for the current executor state
  - patched the MCU version metadata path:
    - dirty builds no longer append the workstation hostname
    - U1 MCU configs no longer ship the all-zero USB product suffix placeholder
  - found the reboot persistence bug for custom main-MCU flashes:
    - `S60klipper` runs `systemUpgrade.sh check-restore` on startup
    - the stock SoC image still shipped `/home/lava/firmware_MCU/VERSION` as
      `20260323110253-51d366c286`
    - after manually flashing a custom `mcu0`, startup treated it as mismatched
      and restored it back to stock on the next Klipper start/reboot
    - the SoC build now stages the local `out_at32f403a/at32f403a.bin` into
      `/home/lava/firmware_MCU/at32f403a.bin`, rewrites
      `/home/lava/firmware_MCU/VERSION` and `md5sum.txt`, and also rewrites the
      top-level upgrade bundle `at32f403a.bin` plus `MCU_DESC` to
      `19700101000000-localbuild`
  - found the second-stage reboot delay bug after that persistence fix:
    - `systemUpgrade.sh` still used the same single expected version for `mcu0`
      and `head0..head3`
    - with custom `mcu0=localbuild` and stock toolheads still on
      `20260323110253-51d366c286`, `check-restore` kept trying to reconcile the
      head MCUs on every boot, which looked like a long loop/recovery cycle
    - the correct fix is to keep checks enabled and split the expected versions
      into `VERSION_MAIN` and `VERSION_HEAD`
  - implemented that split-version boot fix in the image build:
    - `systemUpgrade.sh` now resolves expected MCU versions per board type
    - the SoC image now stages `VERSION_MAIN=19700101000000-localbuild`
    - the SoC image now stages `VERSION_HEAD=20260323110253-51d366c286`
    - rebuilt `tmp/firmware/update.img` and `firmware/firmware.bin`
    - verified the new files directly in `tmp/firmware/rootfs`
  - verified on hardware that the split-version boot fix works:
    - normal boot time restored
    - `show-status` now keeps `Main MCU = localbuild`
    - head MCUs remain on stock `51d366c286`
    - no `skip_checking_mcu` workaround needed
  - ran the first baseline executor test on hardware:
    - command starts now, so protocol/version mismatch is solved
    - failure moved to runtime/electrical behavior
    - observed failure:
      - `Unable to obtain 'spi_transfer_response' response`
      - `GSTAT reset=1 uv_cp=1 vm_uvlo=1`
  - captured the pre-failure idle TMC state:
    - `GSTAT=0`
    - `GCONF=0x00000008`
    - `CHOPCONF ... mres=2(64usteps) intpol=1`
    - `DRV_STATUS ... cs_actual=0(Reset?) stst=1`
    - `MSCNT=1022`
  - added a lower-rate executor retest path on the host side:
    - `MOTOR_PHASE_EXEC_RUN` now accepts `PHASE_STRIDE`
    - default `PHASE_STRIDE=16`
    - latest SoC image for this retest is rebuilt
- Stopped at:
  - boot/version path is fixed and hardware-verified
  - baseline executor still fails electrically/runtime-wise
  - the stride-based retest image is built, but not yet flashed/tested
- Next step:
  - flash the latest SoC image only
  - rerun the baseline executor with reduced update density:
    - `MOTOR_PHASE_EXEC_RUN STEPPER=stepper_y SPEED=30 DISTANCE=20 COIL_SCALE=40 PHASE_STRIDE=16`
  - validate two gates before adding any correction mode:
    1. baseline motion is audibly smooth
    2. direct-mode exit returns cleanly to normal step/dir operation
  - if executor still trips the TMC driver, capture immediately:
    - `GSTAT`
    - `DRV_STATUS`
    - `MSCNT`
  - if the stride-based retest still fails, redesign before correction mode:
    - current timer->pending->DECL_TASK SPI update path is not sufficient
  - build/flash split:
    - SoC/host image:
      - `./dev.sh make build PROFILE=extended`
      - flash via `systemUpgrade.sh upgrade soc /tmp/update.img`
    - mainboard MCU:
      - `make CPP=arm-none-eabi-cpp clean`
      - `make CPP=arm-none-eabi-cpp`
      - `install -D -m 755 out/klipper.bin out_at32f403a/at32f403a.bin`
      - flash via `systemUpgrade.sh upgrade mcu0 /tmp/at32f403a.bin`
- Open blockers:
  - direct-mode enable/disable sequencing is only partially hardened; host now
    aligns entry to current `MSCNT`, but hardware still needs to prove the
    executor can enter/exit without `GSTAT reset/drv_err/vm_uvlo`
  - current executor uses a single `pending` flag between timer ISR and task; if
    the task misses deadlines, multiple phase ticks collapse into one SPI write
    and motion quality may degrade
  - even with the boot/version path fixed, the current baseline executor can
    still trip TMC undervoltage/reset faults during runtime
- Decisions made this session:
  - a naive timed `direct_mode` overlay on top of normal step/dir motion is not credible
  - pre-scheduling SPI writes alone is not enough
  - the next real runtime test requires a true dedicated motion/execution engine
  - DIRECT_MODE register format verified: 9-bit signed coil_a (bits 8:0) and
    coil_b (bits 24:16)
  - baseline table stays in MCU flash; host upload not required for Phase 3
    baseline prototype
  - executor uses own SPI OID (not shared with the existing TMC2240 SPI config)
  - the current timer->pending->task design is acceptable for a first baseline
    test, but only if hardware proves it does not visibly or audibly coalesce
  - MCU dirty-build strings should keep a timestamp but must not expose the
    build workstation hostname
  - the SoC image must ship the same main-MCU bundle metadata as the custom
    flashed executor MCU, otherwise startup `check-restore` silently reverts it
    to stock
  - startup checks must remain active; the mixed-version dev setup needs
    `VERSION_MAIN` and `VERSION_HEAD`, not `skip_checking_mcu`
  - the current next experiment is a reduced-rate executor retest with
    `PHASE_STRIDE=16` before attempting deeper MCU-side redesign

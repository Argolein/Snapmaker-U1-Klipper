# PLANS_Stealthmode.md

## Objective
Add a runtime `Stealth` mode to the Snapmaker U1 Klipper fork.

Target behavior:
- switch between `normal` and `stealth` via G-Code during idle or print
- on `stealth`, apply hard motion caps that cannot be exceeded by slicer G-Code,
  macros, or Python code paths while the mode is active
- on `normal`, restore the prior non-stealth motion settings
- after restart, default back to `normal`
- support separate normal/stealth pressure advance values per extruder

---

## Open questions
- None currently blocking.

---

## Approved plan

### Phase 1 - Config and runtime object
1. Add a new `[stealth_mode]` config section and loadable Klipper extra module
2. Define config keys:
   - `velocity: 120`
   - `accel: 2500`
3. Register a new command:
   - `SET_STEALTH_MODE MODE=STEALTH|NORMAL`
   - if called without `MODE`, report current state

Acceptance checks:
- Klipper loads with `[stealth_mode]`
- `SET_STEALTH_MODE MODE=STEALTH` and `... MODE=NORMAL` are recognized
- startup state is `normal`

### Phase 2 - Hard motion caps
1. Rework `toolhead` motion-limit storage so requested values and effective
   values are separated
2. While stealth mode is active, clamp effective toolhead `max_velocity` and
   `max_accel` to the configured stealth limits
3. Ensure the clamp applies even when other code paths assign directly to
   `toolhead.max_velocity` / `toolhead.max_accel`

Acceptance checks:
- `SET_VELOCITY_LIMIT` cannot push effective velocity above stealth limit
- `M204` cannot push effective acceleration above stealth limit
- internal Python code paths writing `toolhead.max_accel` are still clamped
- leaving stealth restores the requested non-stealth values

### Phase 3 - Runtime transition behavior
1. Drain planned motion before switching mode so the new limits and PA profile
   start at a clean boundary
2. Switch X/Y TMC2240 drivers into StealthChop while Stealth mode is active
   and restore the prior normal driver state when leaving Stealth

Acceptance checks:
- entering stealth performs a motion sync before applying the new mode
- entering stealth enables X/Y StealthChop after the motion sync
- leaving stealth restores the previous X/Y driver state before uncapping motion
- leaving stealth restores the requested non-stealth motion limits

### Phase 4 - Pressure advance dual-profile support
1. Extend extruder config with optional `pressure_advance_stealth`
2. Extend `SET_PRESSURE_ADVANCE`:
   - `ADVANCE` updates the normal profile
   - `STEALTH` updates the stealth profile
   - `SMOOTH_TIME` remains shared
3. Apply the active profile automatically on mode switch

Acceptance checks:
- extruders can store separate normal and stealth PA values
- `SET_PRESSURE_ADVANCE ADVANCE=<normal> STEALTH=<stealth>` stores both
- switching mode swaps the active PA value without requiring another G-Code command

---

## File-level change list
- `klippy/extras/stealth_mode.py`
- `klippy/toolhead.py`
- `klippy/kinematics/extruder.py`
- `lava/printer.cfg`
- `README.md`
- `overlays/firmware-extended/01-repo-host-sync/scripts/01-sync-lava-config.sh`
- `overlays/firmware-extended/01-repo-host-sync/root/etc/init.d/S48-sync-klipper-host-files.sh`
- `overlays/firmware-extended/02-firmware-config/root/usr/local/share/firmware-config/extended/klipper/stealth_mode.cfg`

---

## Risk areas
- making toolhead caps truly non-overridable without breaking existing helper code
- pressure-advance profile semantics must remain backward-compatible for normal mode

---

## Rollback strategy
- remove `[stealth_mode]` from config
- remove `stealth_mode.py`
- revert toolhead/extruder changes
- fallback behavior is the current U1 motion-limit path

---

## Implementation status
- [x] Requirements clarified
- [x] Feature plan updated
- [x] Runtime stealth mode implemented
- [x] Pressure advance dual-profile implemented
- [x] Config updated
- [x] Targeted Python syntax verification complete
- [x] Local mainboard MCU build completed
- [x] Docker full firmware package build completed
- [x] README usage docs added

---

## Decisions
- scope is X/Y runtime Stealth mode with hard motion caps and X/Y TMC2240
  StealthChop switching
- hard caps cover only velocity and acceleration
- config uses a dedicated `[stealth_mode]` section
- runtime command is `SET_STEALTH_MODE MODE=STEALTH|NORMAL`
- pressure advance supports separate normal and stealth values
- `pressure_advance_smooth_time` stays shared
- mode switching must sync/drain planned motion before applying the new mode
- after restart, startup mode is always `normal`
- keep `toolhead.max_velocity` / `toolhead.max_accel` as requested values for
  existing save/restore helper code; motion planning uses separate effective
  capped values
- keep legacy `toolhead` status keys `max_velocity` / `max_accel` as requested
  values too; expose capped runtime limits as `effective_max_velocity` /
  `effective_max_accel`
- use conservative U1 Stealth defaults:
  - velocity `120`
  - accel `2500`

---

## Notes
- Prusa reference behavior validated from `Prusa-Firmware-Buddy`:
  stealth mode stores user motion settings separately and applies hard working
  limits with `min(user_setting, stealth_limit)`.
- Runtime TMC StealthChop switching was reintroduced for X/Y after hardware
  testing direction changed. The implementation drains planned motion first,
  enables X/Y `en_pwm_mode` only while Stealth is active, and restores the
  previous normal driver state before removing the Stealth motion caps.
- Homing, bed-mesh, probing, docking, and similar helper paths should remain
  mostly untouched. The hard cap belongs in `toolhead.Move`, because normal
  helper code eventually routes motion through `toolhead.move()` or
  `toolhead.drip_move()`.
- Verification on 2026-04-24:
  - targeted `python3 -m py_compile` passed for the touched Python files and
    nearby motion/recovery consumers
  - `git diff --check` passed
  - local mainboard MCU build produced `out/klipper.bin`
  - Docker-based SoC/full firmware build produced `firmware/firmware.bin`
  - packaging sync list was extended to include `klippy/kinematics/extruder.py`
    and `klippy/extras/stealth_mode.py`
  - README usage documentation was added for Stealth mode and dual pressure
    advance profiles
  - Stealth config was also added as an extended Klipper include so upgrades
    with existing `/home/lava/printer_data/config/printer.cfg` still load the
    `[stealth_mode]` section
  - Full-UPFILE packaging was changed to SquashFS xz with 1 MiB blocks because
    the gzip-packed extended image exceeded the stock on-device `upgrade all`
    unpack path's practical limit; the U1 kernel has `CONFIG_SQUASHFS_XZ=y`
  - the rebuilt `firmware.bin` was validated with Snapmaker's own
    `upfileUnpack` from the target rootfs in the ARM64 Docker environment
  - updated Stealth defaults to `velocity: 120`, `accel: 2500`
  - reintroduced runtime X/Y StealthChop switching with restore of the prior
    normal TMC2240 `en_pwm_mode` state
  - final artifact hashes:
    - `firmware/firmware.bin`
      `f836a68a4cb7951f963f388a67957b2785af60420e5582ac37bb2b5f7948ecbe`
    - `tmp/firmware/update.img`
      `5bd64ed0eca0e2f4d7cb54b23a6aa42b8bdf7cfadd5c2c2b27a5df0bdf19156f`

## Handoff
- Agent: Codex
- Date: 2026-04-24
- Completed this session:
  - implemented Stealth mode with hard motion caps and X/Y StealthChop
    switching
  - added central requested/effective toolhead motion limits
  - added separate normal/stealth pressure advance profile handling
  - fixed status reporting to avoid helper modules overwriting requested limits
  - ran syntax checks, diff check, and local MCU build
  - added README documentation for Stealth mode and pressure advance commands
- Stopped at:
  - Docker-based firmware packaging completed
- Next step:
  - flash/test on printer if the current diff is accepted
- Open blockers:
  - hardware validation on the printer not yet performed
- Decisions made this session:
  - keep legacy `toolhead` status `max_velocity` / `max_accel` as requested
    values and expose capped values via `effective_max_velocity` /
    `effective_max_accel`
  - ship `[stealth_mode]` through the extended include system as well as
    `origin_printer_data`, because normal upgrades do not necessarily replace
    the active user `printer.cfg`

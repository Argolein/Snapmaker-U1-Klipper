
#  Klipper for Snapmaker U1

This is a project developed based on [Klipper](https://www.klipper3d.org/) specifically for Snapmaker U1.

[![Klipper](docs/img/klipper-logo-small.png)](https://www.klipper3d.org/)

Klipper is a 3d-Printer firmware. It combines the power of a general purpose computer with one or more micro-controllers. See the [features document](https://www.klipper3d.org/Features.html) for more information on why you should use Klipper.

Klipper is Free Software. See the [license](COPYING) or read the [documentation](https://www.klipper3d.org/Overview.html).

## Custom U1 Image Build

This repository can now build a complete Snapmaker U1 host firmware image on top of the official `1.2.0.106` release. The current `extended` profile includes the selected imported features from the paxx12 Extended Firmware work:

- OEM disk usage support in Moonraker
- Remote Screen
- Extended config include support
- `curl`
- IPv6 disable
- Camera FPS / `v4l2-mpp` integration
- USB Ethernet support, including Realtek USB NIC firmware files

### Prerequisites

- Docker Desktop or another working Docker daemon
- An ARM64-capable Docker environment

The image builder chroots into the extracted U1 root filesystem, so the Dockerized path is the supported build route.

### Build Commands

```bash
./dev.sh make tools
./dev.sh make firmware
./dev.sh make build PROFILE=extended
```

Useful inspection commands:

```bash
./dev.sh make extract PROFILE=extended
make overlays PROFILE=extended
```

### Build Outputs

- Full packed firmware: `firmware/firmware.bin`
- Rebuilt Rockchip upgrade image: `tmp/firmware/update.img`
- Rebuilt rootfs for inspection: `tmp/firmware/rootfs/`

The rebuilt rootfs is packed as SquashFS xz. The U1 kernel enables xz support,
and the smaller image keeps the full `upgrade all` package within the stock
on-device unpack path's practical limits.

### Flashing

For development, the repository now has a verified build path for the SoC image. The on-device upgrade script extracted from the official firmware supports these commands:

```bash
scp tmp/firmware/update.img root@<u1-ip>:/tmp/
ssh root@<u1-ip> /home/lava/bin/systemUpgrade.sh upgrade soc /tmp/update.img
```

The same upgrade script also advertises a full-UPFILE path. For host-only
changes such as Runtime Stealth Mode, prefer `upgrade soc`; a full upgrade may
also flash MCU payloads and is unnecessary unless that is intentional:

```bash
scp firmware/firmware.bin root@<u1-ip>:/tmp/upgrade.bin
ssh root@<u1-ip> /home/lava/bin/systemUpgrade.sh upgrade all /tmp/upgrade.bin
```

The software-side build and repack flow is validated in this repository. Hardware flashing and feature verification on a real printer are still pending.

## Runtime Stealth Mode

This fork includes a runtime Stealth mode. It applies hard motion caps in
Klipper's central toolhead planner, switches the X/Y TMC2240 drivers into
StealthChop while Stealth mode is active, and can use a separate pressure
advance profile.

The default limits are configured in `lava/printer.cfg`:

```ini
[stealth_mode]
velocity: 120
accel: 2500
```

### Mode Commands

Enable Stealth mode:

```gcode
SET_STEALTH_MODE MODE=STEALTH
```

Return to Normal mode:

```gcode
SET_STEALTH_MODE MODE=NORMAL
```

Report the current mode:

```gcode
SET_STEALTH_MODE
```

On every mode switch, Klipper waits for already planned moves to finish before
applying the new limits and pressure advance profile. After a restart, the
printer always starts in Normal mode.

While entering Stealth mode, Klipper stores the current X/Y `en_pwm_mode`
driver state and sets it to StealthChop. While leaving Stealth mode, the stored
normal driver state is restored before the higher Normal-mode motion limits are
made effective again.

While Stealth mode is active, slicer G-code, macros, and normal Python helper
paths may request higher velocity or acceleration values, but actual X/Y
toolhead moves are capped by the Stealth limits. Low-level direct-stepper
features such as `FORCE_MOVE` or `manual_stepper` bypass the normal toolhead
planner and are not part of this safety guarantee.

### Pressure Advance Profiles

Normal Klipper pressure advance still works:

```gcode
SET_PRESSURE_ADVANCE ADVANCE=0.040
```

This fork also supports a separate Stealth pressure advance value:

```gcode
SET_PRESSURE_ADVANCE ADVANCE=0.040 STEALTH=0.060
```

`ADVANCE` updates the Normal-mode value. `STEALTH` updates the Stealth-mode
value. `SMOOTH_TIME` remains shared between both modes:

```gcode
SET_PRESSURE_ADVANCE ADVANCE=0.040 STEALTH=0.060 SMOOTH_TIME=0.040
```

If `SET_PRESSURE_ADVANCE STEALTH=...` is called while the printer is currently
in Normal mode, the Stealth value is stored but the active pressure advance does
not change until Stealth mode is enabled. The same applies in reverse for
`ADVANCE=...` while Stealth mode is active.

## Development

### Host Software

#### Prepare the Cross-compiler

A portion of the host-side code is written in C language and is located in the directory klippy/chelper. To enable this part of the code to run on the main chip of U1, it needs to be compiled using the cross-compiler for the U1 main chip.

The cross-compiler on the U1 host uses aarch64-none-linux-gnu, and its version number is 12.3.rel1. You can download it [here](https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads). Find the link for the corresponding compilation platform and then download and install it.

#### Compile chelper

Taking the x86_64 Linux compilation environment as an example, suppose aarch64-none-linux-gnu is located in the directory /path/to/arm-gnu-toolchain-12.3.rel1-x86_64-aarch64-none-linux-gnu, and klipper project is located in the directory /path/to/klipper

```
cd /path/to/klipper
make -C klippy/chelper CROSS_COMPILE=/path/to/arm-gnu-toolchain-12.3.rel1-x86_64-aarch64-none-linux-gnu/bin/aarch64-none-linux-gnu-
```

The c_helper.so file was generated in the klippy/chelper directory.

#### Install

The Klipper host software can be installed onto the target system by simply copying it.

- First, you need to log in to the system and stop the klipper process

  ```
  /etc/init.d/S60klipper stop
  ```

- Second, copy the python source files and c_helper to the specified target directory /home/lava/klipper.

- Third, restart the device to start the updated Klipper host. The device uses an overlay file system, so a temporary file /oem/.debug needs to be created to ensure that the updated klipper host is not overwritten.

### Micro-controller Software

#### Prepare the Cross-compiler

Similar to the host software, the micro-controller Software uses gcc-arm-none-eabi to compile the C source program. Its version number is 10.3-2021.10. You can download it [here](https://developer.arm.com/downloads/-/gnu-rm). Find the link for the corresponding compilation platform and then download and install it.

#### Compile

Taking the x86_64 Linux compilation environment as an example, suppose gcc-arm-none-eabi is located in the directory /path/to/gcc-arm-none-eabi-10.3-2021.10-x86_64-linux, and klipper is located in the directory /path/to/klipper.

**Mainboard micro-controller compilation**

```
cd /path/to/klipper
rm -f .config .config.old
cp -f lava/at32f403a_config .config
make CROSS_PREFIX=/path/to/gcc-arm-none-eabi-10.3-2021.10-x86_64-linux/bin/arm-none-eabi- OUT=out_at32f403a/
cp -f out_at32f403a/klipper.bin out_at32f403a/at32f403a.bin
```

**Extruder micro-controller compilation**

```
cd /path/to/klipper
rm -f .config .config.old
cp -f lava/at32f415_config .config
make CROSS_PREFIX=/path/to/gcc-arm-none-eabi-10.3-2021.10-x86_64-linux/bin/arm-none-eabi- OUT=out_at32f415/
cp -f out_at32f415/klipper.bin out_at32f415/at32f415.bin
```

#### Install

- First, copy the firmware files at32f415.bin and at32f403a.bin to the device.

- Second, you need to log in to the system and stop the klipper process

  ```
  /etc/init.d/S60klipper stop
  ```

- Third, burn the firmware into the microcontroller. Suppose at32f403a.bin and at32f415.bin are located in the /tmp directory.

    - burn firmware onto the motherboard microcontroller.

      ```
      systemUpgrade.sh upgrade mcu0 /tmp/at32f403a.bin
      ```

    - burn firmware onto the first extruder microcontroller.

      ```
      systemUpgrade.sh upgrade head0 /tmp/at32f415.bin
      ```

      For the other extruders, the "head0" parameter needs to be replaced to "head1", "head2" or "head3".

- Fourth, restart the firmware or restart the device. A temporary file "/oem/.skip_checking_mcu" needs to be created to disable the firmware matching check.

# PLANS.md

## Objective
Import the selected paxx12 Extended Firmware changes into this official Snapmaker U1 Klipper repo so that this repo becomes the working source of truth for:

1. Snapmaker U1 host and MCU sources
2. building a flashable Snapmaker-style `upgrade.bin` image
3. flashing that image to the printer with the selected community features included

Scope for this work:
- only the 7 selected features
- plus the minimal support layers and external dependencies required to make those 7 features work correctly

End state:
- this repo can build the firmware image
- the built image is flashable
- the selected features are owned and reproducible from this repo

---

## Architecture

### Validated facts
- This repo already contains Snapmaker U1-specific Klipper host and MCU sources and configs.
- The Extended Firmware repo provides a working image-builder pipeline:
  1. download official firmware
  2. unpack Snapmaker/Rockchip container
  3. extract squashfs rootfs
  4. apply overlays
  5. rebuild squashfs
  6. repack firmware image
- The required builder infrastructure exists in `SnapmakerU1-Extended-Firmware`:
  - `tools/rk2918_tools/`
  - `tools/upfile/`
  - `tools/resource_tool/`
  - `scripts/create_firmware.sh`
  - `scripts/extract_squashfs.sh`
  - `scripts/helpers/`
  - `.github/dev/Dockerfile`
  - `vars.mk`
  - `dev.sh`
- `create_firmware.sh` requires root and relies on `unsquashfs`, `mksquashfs`, `patch`, `file`, `wget`, `git`, and helper scripts.
- The current Extended Firmware build environment is Debian Trixie ARM64 in Docker.
- The Remote Screen and Camera features are not standalone. They depend on support layers outside their main overlays.
- Klipper wildcard includes like `[include extended/klipper/*.cfg]` do allow an empty match set. Keep files are optional for parser behavior, but still useful as placeholders.

### Target architecture
- This repo becomes the source of truth for the selected feature set.
- The image build pipeline is ported into this repo.
- The base image still starts from the official Snapmaker firmware download.
- The selected paxx12 features are imported into this repo as repo-owned overlays, patches, scripts, and support files.
- The resulting image must reflect files owned by this repo, not only stock firmware plus runtime patches.
- To achieve that, this repo needs a dedicated host-sync overlay that copies the repo-owned host-side Klipper payload into the image where applicable, instead of relying only on the stock `/home/lava/klipper` from the downloaded firmware.

### Allowed support layers
The user approved importing the minimal support layers required by the chosen features:
- `02-firmware-config` subset
- `common/04-nginx-fluidd.d`
- `common/05-persist-dhcp`
- USB NIC firmware blobs from commit `ccde0b8`

---

## Open questions
- None currently blocking.

---

## Decisions
- **Output**: flashable Snapmaker U1 `upgrade.bin` image built from this repo
- **Base image**: official Snapmaker firmware downloaded from Snapmaker CDN
- **Repo role**: this repo must own both the imported feature implementation and the image build logic
- **Host integration**: host-side files from this repo must be injected into the firmware image via repo-owned overlays where needed
- **Support layers**: minimal required support overlays may be imported when necessary to make the 7 selected features work
- **USB NIC compatibility**: include the USB NIC firmware blob overlay from `ccde0b8`
- **Build environment**: Docker-based ARM64 development environment, following the Extended Firmware model
- **Feature 3 includes**: placeholder keep files may be kept for clarity, but they are not required to avoid a Klipper wildcard-include error
- **Camera build model**: `v4l2-mpp` is not a mystery prebuilt binary; the current Extended Firmware builds it from a pinned external git repository during image build
- **Motor phase runtime gate**: the current printer-side `H2/H4` execution-near gate (`selected_score mean ~0.876`) is good enough to justify the next runtime-nearer prototype, but it is still not a benefit claim
- **Rejected motor phase paths**: both the host-`dwell()` direct-mode path and the pre-scheduled SPI direct-mode path are rejected as credible benefit-test paths
- **Next motor phase architecture step**: the next credible runtime step is a dedicated one-motor executor on the mainboard MCU near the executed-step path, not another host/SPI sequencing refinement
- **Next motor phase executor scope**: keep the first executor limited to `stepper_y @ 30 mm/s`, `baseline_direct_profile` first, then the frozen `H2/H4` correction profile only after baseline smoothness is proven
- **TMC2240 DIRECT_MODE register format**: register 0x2D; coil_a bits[8:0] 9-bit signed (−255..+255), coil_b bits[24:16] 9-bit signed (−255..+255); SPI write is 5 bytes [0xAD, MSB..LSB]
- **MCU executor ISR constraint**: spidev_transfer() is blocking; executor timer ISR only sets pending flag and wakes task; DECL_TASK does the actual SPI write
- **Baseline table location**: ideal sin/cos table (1024 entries) lives in MCU flash as compile-time const; no host upload needed for the baseline-only Phase 3 prototype
- **Executor SPI OID**: executor uses its own `config_motor_phase_exec`-registered spidev_s OID, not the existing TMC2240 SPI config OID
- **MCU version checks must stay enabled**: the real fix for the slow reboot/recovery loop is a split version contract (`VERSION_MAIN` for `mcu0`, `VERSION_HEAD` for `head0..head3`), not `/oem/.skip_checking_mcu`
- **Boot-loop root cause**: `systemUpgrade.sh` originally used one global `/home/lava/firmware_MCU/VERSION` for both the custom main MCU and the stock head MCUs, so `check-restore` kept treating the unchanged heads as mismatched during boot

---

## Validated dependencies and prerequisites

### Build-time infrastructure
- Docker
- root privileges inside the build environment
- `SYS_ADMIN` capability for squashfs/chroot operations
- Builder tools from the Extended Firmware repo

### Packages installed by the current Extended Firmware dev image
- `build-essential`
- `cmake`
- `pkg-config`
- `squashfs-tools`
- `git-core`
- `bc`
- `flex`
- `bison`
- `libssl-dev`
- `dos2unix`
- `sudo`
- `sshpass`
- `unzip`
- `wget`
- `g++-aarch64-linux-gnu`
- `gcc-aarch64-linux-gnu`
- `file`
- `golang-go`
- `ffmpeg`
- `u-boot-tools`
- `ccache`
- `libssl-dev:arm64`

### External sources currently used by the selected features
- official Snapmaker firmware download from `vars.mk`
- `deps/screen-apps/` for Remote Screen
- `https://github.com/paxx12/v4l2-mpp.git` pinned in the camera overlay script
- static curl release archive from `stunnel/static-curl`

### Required support layers identified during validation
- `02-firmware-config` subset:
  - `01-add-klipper-includes.patch`
  - `01-add-moonraker-includes.patch`
  - `extended-config.py`
  - `extended/extended.cfg`
  - keep/config seed files under `extended/klipper/` and `extended/moonraker/`
- `common/04-nginx-fluidd.d`
  - required because Remote Screen now installs `remote-screen.conf` and `auth-check.conf` under `/etc/nginx/fluidd.d/`
- `common/05-persist-dhcp`
  - required so DHCP lease state survives reboots for USB ethernet
- `common/07-persist-ssh-hostkeys`
  - required so enabling official SSH on the printer keeps a stable host key across reboots
- `ccde0b8`
  - required for broader USB NIC compatibility

### Architecture/runtime constraints
- The current builder uses `chroot` into the extracted ARM64 rootfs and executes commands there.
- Remote Screen currently runs `pip3 install` inside the ARM64 rootfs during build.
- That means the Docker build environment must actually be able to execute ARM64 binaries in the chroot.
- The Extended Firmware documentation assumes an ARM64 Docker environment. Running the same process on x86_64 would require additional emulation/binfmt work that is not yet part of the approved plan.

---

## Feature inventory

### Feature 1 — OEM Disk Usage
**Source commit**: `858b4bf`

**Validated implementation**:
- `overlays/firmware-extended/12-patch-moonraker/patches/01-add-oem-disk-usage-support.patch`

**Risk**: Medium
- Moonraker failure affects web UI/API, but not basic printer firmware operation

### Feature 2 — Remote Screen
**Source commit**: `d1b033e`

**Validated current implementation**:
- overlay now lives conceptually in `61-app-remote-screen`
- depends on:
  - `deps/screen-apps/`
  - `common/04-nginx-fluidd.d`
  - `02-firmware-config` subset
- validated files in current Extended repo include:
  - `/etc/init.d/S99fb-http`
  - `/etc/nginx/fluidd.d/remote-screen.conf`
  - `/etc/nginx/fluidd.d/auth-check.conf`
  - `/usr/local/share/fb-http/html/*`
  - `extended/moonraker/04_remote_screen.cfg`

**Important correction**:
- this is no longer just a direct `printer.cfg` edit plus a standalone Python script
- current implementation uses include-driven config plus nginx sidecar config files

**Risk**: Medium
- nginx or auth integration errors can break the web UI

### Feature 3 — Extended config directory
**Validated implementation path**:
- `02-firmware-config/patches/01-add-klipper-includes.patch`
- `02-firmware-config/patches/01-add-moonraker-includes.patch`
- keep files:
  - `extended/klipper/00_keep.cfg`
  - `extended/moonraker/00_keep.cfg`

**Important correction**:
- wildcard includes do not require a non-empty stub to avoid parser failure
- keep files remain useful as explicit placeholders and seed content

**Risk**: Low

### Feature 4 — Add curl
**Source commit**: `9fd81e0`

**Validated current implementation**:
- `01-system-utils/scripts/curl_install.sh`
- current validated SHA256:
  - `3c6562544e1a21cd37e9dec7c48c7a6d9a2f64da42fde69ba79e54014b911abb`

**Important correction**:
- the SHA256 previously recorded in this plan was incorrect

**Risk**: Low

### Feature 5 — Disable IPv6
**Source commit**: `557e30a`

**Validated historical implementation**:
- standalone overlay adding `/etc/sysctl.d/90-disable-ipv6.conf`

**Risk**: Low

### Feature 6 — Camera FPS improvements
**Validated source commits**:
1. `e5c588a` — Fluidd camera feed base
2. `26103d7` — USB camera support
3. `0534964` — v4l2-mpp with WebRTC
4. `34e9d9f` — use WebRTC for camera
5. `7d53571` — fix WebRTC overlay
6. `3c409bf` — USB camera fixes and reconnect/error handling
7. `fb69f54` — update `capture-v4l2-raw|jpeg-mpp`
8. `07cb908` — WebRTC and RTSP resilience
9. `d6518b6` — retry support

**Validated current implementation dependencies**:
- `60-app-camera/scripts/01-v4l2-mpp.sh`
- `60-app-camera/scripts/02-extra-apps.sh`
- `60-app-camera/root/etc/init.d/S99v4l2-mpp-mipi`
- `60-app-camera/root/etc/init.d/S99v4l2-mpp-usb`
- `60-app-camera/root/etc/udev/rules.d/99-v4l2-mpp-usb.rules`
- `60-app-camera/root/usr/local/share/firmware-config/extended/moonraker/02_internal_camera.cfg`
- `60-app-camera/root/usr/local/share/firmware-config/extended/moonraker/03_usb_camera.cfg`
- external git dependency:
  - `https://github.com/paxx12/v4l2-mpp.git`
  - pinned at `10fc3b9d935d9c79bacc014839c05de4a004c4ac` in the current script

**Important correction**:
- this is not blocked on “is there a precompiled binary?”
- it is blocked only by integration complexity and external build dependency management

**Risk**: High

### Feature 7 — USB Ethernet
**Validated source commits**:
1. `d98529d` — eth0 base support
2. `7087081` — DHCP on USB ethernet
3. `8d45307` — improved eth0 support
4. `8487185` — udev rules for eth0 and USB camera
5. `546d577` — persist random eth0 MAC
6. `ccde0b8` — common USB NIC firmware blobs

**Validated support dependency**:
- `common/05-persist-dhcp`

**Risk**: Medium-High
- network misconfiguration can impact connectivity and recovery workflows

---

## File-level change list

### Image-builder infrastructure to import
- `tools/rk2918_tools/`
- `tools/upfile/`
- `tools/resource_tool/`
- `scripts/create_firmware.sh`
- `scripts/extract_squashfs.sh`
- `scripts/helpers/`
- `.github/dev/Dockerfile`
- `vars.mk`
- `dev.sh`

### New repo areas expected in this repo
- `overlays/`
- `firmware/` during builds
- `tmp/` during builds
- `deps/screen-apps/`

### Existing repo files expected to change
- `Makefile`
  - add image-builder targets without breaking current MCU build targets
- possibly README/build docs
  - document image build flow and prerequisites

### Repo-owned host integration expected
- add a dedicated overlay that syncs repo-owned host files into the firmware image
- likely source areas:
  - `klippy/`
  - `lava/`
  - built `klippy/chelper`
  - any additional host scripts/config files required for the selected features

---

## Approved plan

### Phase 0 — Plan validation
1. Validate Claude's findings against this repo and `SnapmakerU1-Extended-Firmware`
2. Correct `PLANS.md`
3. Record missing dependencies and support overlays

**Acceptance checks**:
- plan no longer contains the wildcard-include error claim
- support overlay dependencies are explicit
- architecture is consistent with the repo-owning-the-result goal

### Phase 1 — Port image-builder foundation
1. Import builder tools, scripts, helper scripts, Dockerfile, `vars.mk`, and `dev.sh`
2. Extend the existing `Makefile` with image-builder targets:
   - `tools`
   - `firmware`
   - `build`
   - `extract`
3. Ensure current MCU build flow is not broken by the new targets/names
4. Port `deps/screen-apps/`
5. Add baseline `overlays/` structure

**Acceptance checks**:
- image-builder targets run from this repo
- `make firmware` downloads and verifies the official image
- `make extract` unpacks the image successfully
- `make tools` builds the imported image tools successfully

### Phase 2 — Port support layers required by the selected features
1. Import the minimal `02-firmware-config` subset
2. Import `common/04-nginx-fluidd.d`
3. Import `common/05-persist-dhcp`
4. Import USB NIC firmware blob support from `ccde0b8`
5. Add a repo-owned host-sync overlay so the image can include files from this repo where required

**Acceptance checks**:
- extracted rootfs contains:
  - include hooks for `extended/klipper/*.cfg`
  - include hooks for `extended/moonraker/*.cfg`
  - nginx `fluidd.d` include support
  - DHCP persistence init script
  - USB NIC firmware support files

### Phase 3 — Implement low-risk features
1. Feature 3: extended include support
2. Feature 5: disable IPv6
3. Feature 4: curl

**Acceptance checks**:
- extracted rootfs shows the expected files and config hooks
- curl binary is present and executable in the image rootfs
- IPv6 sysctl file is present

### Phase 4 — Implement medium-risk features
1. Feature 1: OEM Disk Usage
2. Feature 7: USB Ethernet, including:
   - eth0 config
   - DHCP persistence
   - udev rules
   - MAC persistence
   - USB NIC firmware blobs

**Acceptance checks**:
- Moonraker patch is present in extracted rootfs
- ethernet-related files are present in extracted rootfs
- no obvious conflict with existing Wi-Fi/network files after extraction review

### Phase 5 — Implement complex features
1. Feature 2: Remote Screen
2. Feature 6: Camera FPS improvements

**Acceptance checks**:
- extracted rootfs contains:
  - fb-http service
  - nginx sidecar config
  - screen assets
  - camera init scripts
  - udev rules
  - Moonraker camera config fragments
- build succeeds with external dependencies resolved reproducibly

### Phase 6 — End-to-end validation
1. Build the final image from this repo
2. Inspect extracted rootfs before flashing
3. Flash to printer
4. Verify selected features on hardware
5. Document build and flash workflow in this repo

**Acceptance checks**:
- firmware image builds from this repo
- firmware image flashes successfully
- selected imported features behave as expected on device

---

## Risk areas
- **Architecture drift**: adding image targets to the existing `Makefile` must not break current MCU build behavior
- **Host/source mismatch**: if repo-owned host files are not copied into the image, the result will still effectively be “stock firmware plus overlays”
- **ARM64 execution requirement**: build steps that `chroot` into the ARM64 rootfs may fail on unsupported x86_64 setups
- **Remote Screen**: nginx include support and auth forwarding are easy to miswire
- **Camera**: cross-build plus external `v4l2-mpp` dependency is the most complex feature in scope
- **USB Ethernet**: network changes can reduce device recoverability if done incorrectly
- **Official firmware drift**: future Snapmaker firmware versions may require patch refreshes

---

## Rollback strategy
- keep each imported feature isolated in its own repo-owned overlay where practical
- support overlays remain separable from feature overlays
- if a feature proves unstable, remove that overlay from the build profile and rebuild
- validate changes with `make extract` before flashing
- do not flash an image that has not first been inspected after repack

---

## Exact build workflow

This is the current verified build path for the custom Snapmaker U1 firmware image in this repository.

### 1. Start the supported build environment

- Make sure the Docker daemon is running
- Use the Dockerized builder via `./dev.sh`
- Do not rely on plain host-native `make build` on macOS; the image builder needs the Linux/ARM64 chroot-capable environment from `.github/dev/Dockerfile`

### 2. Know which inputs are used

- Official base firmware comes from `vars.mk`
- The build starts from Snapmaker's stock `U1_1.2.0.106` firmware and repacks it
- Repo-owned config files under `lava/` are copied into the image by `overlays/firmware-extended/01-repo-host-sync/`
- Feature and support changes are applied through `overlays/`
- External dependencies are fetched on demand and cached under `tmp/cache/`
  - `v4l2-mpp`
  - `live555`
  - static `curl`
  - Realtek USB NIC firmware files

### 3. Build commands

Run these commands from the repo root:

```bash
./dev.sh make tools
./dev.sh make firmware
./dev.sh make build PROFILE=extended
make CPP=arm-none-eabi-cpp clean
make CPP=arm-none-eabi-cpp
install -D -m 755 out/klipper.bin out_at32f403a/at32f403a.bin
```

Useful helper commands:

```bash
make profiles
make overlays PROFILE=extended
./dev.sh make extract PROFILE=extended
```

### 4. What each command does

1. `./dev.sh make tools`
   - builds `rk2918_tools`, `upfile`, and `resource_tool`
2. `./dev.sh make firmware`
   - downloads the official Snapmaker firmware from `vars.mk`
   - verifies its SHA256
   - stores it in `firmware/`
3. `./dev.sh make build PROFILE=extended`
   - unpacks the official firmware
   - extracts the root filesystem
   - applies overlays in profile order
   - rebuilds the rootfs
   - repacks `update.img`
   - repacks the final `firmware/firmware.bin`
4. `make CPP=arm-none-eabi-cpp clean && make CPP=arm-none-eabi-cpp`
   - builds the mainboard MCU firmware binary at `out/klipper.bin`
   - `CPP=arm-none-eabi-cpp` is required on macOS (system cpp fails on `.lds.S`)
5. `install -D -m 755 out/klipper.bin out_at32f403a/at32f403a.bin`
   - copies the MCU build artifact to the dedicated flash location used in this repo

### 5. Verified overlay order for `PROFILE=extended`

The current build uses this order:

1. `overlays/common/04-nginx-fluidd.d/`
2. `overlays/common/05-persist-dhcp/`
3. `overlays/common/06-persist-eth0-mac/`
4. `overlays/common/07-persist-ssh-hostkeys/`
5. `overlays/firmware-extended/01-repo-host-sync/`
6. `overlays/firmware-extended/02-firmware-config/`
7. `overlays/firmware-extended/14-patch-firmware-files/`
8. `overlays/firmware-extended/20-disable-ipv6/`
9. `overlays/firmware-extended/21-add-curl/`
10. `overlays/firmware-extended/22-patch-moonraker/`
11. `overlays/firmware-extended/23-usb-ethernet/`
12. `overlays/firmware-extended/33-feature-timelapse-stub/`
13. `overlays/firmware-extended/60-app-camera/`
14. `overlays/firmware-extended/61-app-remote-screen/`

### 6. Build outputs to inspect

- Final packed firmware: `firmware/firmware.bin`
- Repacked SoC update image: `tmp/firmware/update.img`
- Rebuilt rootfs for inspection: `tmp/firmware/rootfs/`
- Cached downloads and source trees: `tmp/cache/`

### 7. What to inspect after changes

- Confirm final build still succeeds with:
  - `./dev.sh make build PROFILE=extended`
- Inspect rebuilt rootfs for the changed feature under:
  - `tmp/firmware/rootfs/`
- Re-check overlay order with:
  - `make overlays PROFILE=extended`
- If the change touches base config hooks, verify:
  - `tmp/firmware/rootfs/home/lava/origin_printer_data/config/printer.cfg`
  - `tmp/firmware/rootfs/home/lava/origin_printer_data/config/moonraker.conf`

### 8. Flash paths currently known

Two flash/update paths are currently documented from the official device scripts:

1. SoC-only update using the rebuilt `update.img`

```bash
scp tmp/firmware/update.img root@<u1-ip>:/tmp/
ssh root@<u1-ip> /home/lava/bin/systemUpgrade.sh upgrade soc /tmp/update.img
```

2. Full UPFILE-style update using `firmware.bin`

```bash
scp firmware/firmware.bin root@<u1-ip>:/tmp/upgrade.bin
ssh root@<u1-ip> /home/lava/bin/systemUpgrade.sh upgrade all /tmp/upgrade.bin
```

Hardware flashing is still pending validation on a real device. Until that is completed, the build path above is verified, but on-device behavior must still be treated as the final acceptance gate.

3. Mainboard-MCU-only update using the separately built `at32f403a.bin`

```bash
scp out_at32f403a/at32f403a.bin root@<u1-ip>:/tmp/
ssh root@<u1-ip> /home/lava/bin/systemUpgrade.sh upgrade mcu0 /tmp/at32f403a.bin
```

Important:
- `./dev.sh make build PROFILE=extended` does not rebuild `src/` MCU changes
- changes under `src/` require the separate root MCU build shown above
- host/SoC and mainboard MCU are therefore two separate build/flash paths

### 8a. Recommended first hardware flash procedure

For the first real device test, prefer the SoC-only path with `update.img`.

Reason:

- the imported changes in this repository are host/rootfs-side
- the stock MCU binaries from the official firmware are still bundled, but there is no need to reflash the MCUs for the first validation pass
- `upgrade soc` limits the blast radius compared to `upgrade all`

Recommended checklist:

1. Make sure the printer is idle
   - no active print
   - stable power
   - network access to the device is working
2. Keep both artifacts available locally
   - custom image: `tmp/firmware/update.img`
   - stock rollback source: re-extract the official image with `./dev.sh make extract PROFILE=extended` and keep the stock `update.img` from the extracted firmware if a rollback is needed
3. Verify the custom build finished successfully
   - `./dev.sh make build PROFILE=extended`
4. Copy the custom SoC image to the printer

```bash
scp tmp/firmware/update.img root@<u1-ip>:/tmp/
```

5. Run the SoC-only upgrade

```bash
ssh root@<u1-ip> /home/lava/bin/systemUpgrade.sh upgrade soc /tmp/update.img
```

6. Wait for the device to reboot completely
7. Validate basic recovery first
   - device boots
   - web UI responds
   - Klipper and Moonraker come up
   - no immediate boot loop
8. Then validate imported features one by one
   - OEM disk usage
   - extended include hooks
   - curl
   - IPv6 disable
   - USB Ethernet
   - Remote Screen
   - Camera

Only after the SoC-only path is confirmed should `firmware.bin` / `upgrade all` be considered.

### 9. Workflow for future feature changes

1. Change repo-owned files in `overlays/`, `lava/`, `deps/`, or build scripts as needed
2. Treat this repository as the canonical U1 integration branch for future work.
   - New U1-specific behavior should land here first.
   - If porting from upstream or mainline Klipper, import only the minimum required deltas and keep them isolated in repo-owned overlays or clearly scoped source changes.
   - Re-check interactions with the Snapmaker base image and existing imported features before flashing.
   - When a host-side Klipper source file is changed, make sure the image build copies that file into `/home/lava/klipper` and record the upstream commit reference when one exists.
3. Re-run:
   - `./dev.sh make build PROFILE=extended`
4. Inspect:
   - `tmp/firmware/rootfs/`
5. If the feature affects runtime config, confirm the copied or patched files in the rebuilt rootfs
6. Only after software inspection, flash and test on hardware

---

## Implementation status
- [x] Phase 0: plan validation and correction
- [x] Phase 1: image-builder foundation
  - [x] Validate source files and required imports from `SnapmakerU1-Extended-Firmware`
  - [x] Import builder tools and helper scripts
  - [x] Integrate image targets into root `Makefile`
  - [x] Add baseline repo structure for `tools/`, `overlays/`, and image builds
  - [x] Verify imported builder commands
    - Verification result:
      - `make profiles` succeeds and lists `extended` / `extended-devel`
      - `make -n tools` dispatches into the imported tool directories correctly
      - `./dev.sh make tools` succeeds in the Docker builder
      - `./dev.sh make firmware` downloads and verifies the official base image
      - `./dev.sh make extract PROFILE=extended` unpacks the firmware and rootfs successfully
- [x] Phase 2: support layers
  - [x] Import minimal `02-firmware-config` subset
  - [x] Import `common/04-nginx-fluidd.d`
  - [x] Import `common/05-persist-dhcp`
  - [x] Import `common/06-persist-eth0-mac`
  - [x] Import `common/07-persist-ssh-hostkeys`
  - [x] Import USB NIC firmware blob support from `ccde0b8`
  - [x] Add a repo-owned host-sync overlay
  - [x] Import `timelapse` compatibility stub to avoid Moonraker/Fluidd plugin warnings with the stock `[timelapse]` printer config
- [x] Phase 3: low-risk features
  - [x] Feature 3: extended include support via `02-firmware-config`
  - [x] Feature 5: disable IPv6
  - [x] Feature 4: curl
  - [x] Full build/repack verification complete with `./dev.sh make build PROFILE=extended`
- [x] Phase 4: medium-risk features
  - [x] Feature 1: OEM Disk Usage overlay imported
  - [x] Feature 7: USB Ethernet overlays imported
  - [x] Full build/repack verification complete with `./dev.sh make build PROFILE=extended`
  - [x] Removed the stale `dhcpcd.conf` patch because stock firmware `1.2.0.106` already includes `allowinterfaces wlan0 eth0`
- [x] Phase 5: complex features
  - [x] Import Feature 2: Remote Screen overlay, assets, and screen-app dependency integration
  - [x] Import Feature 6: Camera overlay, pinned `v4l2-mpp` build, and helper apps
  - [x] Fix the imported Remote Screen HTML path inconsistency to match the installed `screen-apps` layout
  - [x] Set Remote Screen defaults to enabled in the repo-owned `extended.cfg` and default Moonraker fragment for future fresh configs
  - [x] Rename the runtime config from `extended2.cfg` to `extended.cfg` and add one-time migration logic for existing printers
  - [x] Rebuild `firmware/firmware.bin` after enabling Remote Screen defaults and fixing the `timelapse` stub
  - [x] Patch `klippy/toolhead.py` to use `LOOKAHEAD_FLUSH_TIME = 0.150` and extend host-sync so the modified host file is copied into the firmware image
  - [x] Persist Dropbear host keys in `/oem/dropbear` so the official SSH feature keeps a stable host key across reboots
  - [x] Full build/repack verification complete with `./dev.sh make build PROFILE=extended`
- [x] Phase 6: end-to-end validation
  - [x] Final firmware image builds from this repo: `firmware/firmware.bin`
  - [x] Rebuilt rootfs inspected in `tmp/firmware/rootfs`
  - [x] Flash to printer
  - [x] Verify selected features on hardware
  - [x] Document final build/flash workflow
- [x] Post-import repo planning
  - [x] Validate `motor-phase-tuning.md` against this repo and the local Prusa reference
  - [x] Recast `motor-phase-tuning.md` as a feasibility-first plan instead of an implementation-ready task list
- [ ] Motor phase tuning Phase 0
  - [x] Add an initial host-side `motor_phase_calibration` Klipper extra
  - [x] Seed repo config with `[force_move]` and `[motor_phase_calibration]`
  - [x] Extend `tmc2240.py` with `DIRECT_MODE` `coil_a` / `coil_b` fields for debug experiments
  - [x] Validate `MOTOR_PHASE_MEASURE` on hardware
  - [x] Validate `DUMP_TMC` / `SET_TMC_FIELD` direct-mode experiments on hardware
  - [x] Rebuild after the host-file permission fix and the updated measurement return-path logic
  - [x] Reflash and validate the updated measurement return-path logic on hardware
- [ ] Motor phase tuning Phase 1
  - [x] Add a first host-side CSV analyzer script for constant-speed captures
  - [x] Run the analyzer on real U1 CSV files copied off the printer
  - [x] Use the measured sample-rate envelope to define the first bounded sweep range
  - [x] Add automatic safe staging and axis-limit-aware direction handling
  - [x] Add an in-printer bounded sweep command that can sequence multiple measurements
  - [x] Add host-side forward/backward aggregation across repeated runs at the same speed
  - [x] Add host-side basis export for a first small harmonic set from aggregated runs
  - [x] Add host-side normalized fit/LUT export from the aggregated basis
  - [x] Add host-side runtime-payload export for a future U1 integration target
  - [x] Add a first printer-side direct-mode consumer for the runtime-payload prototype
  - [x] Add an in-memory profile layer for loaded runtime payloads
  - [x] Add a step-generation-adjacent diagnostic trace command that exports real `stepcompress` history for a normal move
  - [x] Add a step-execution-near diagnostic trace path with MCU-side sampled execution clocks and step numbers
  - [x] Rebuild fresh host and mainboard-MCU artifacts for the execution-trace slice
  - [x] Add an execution-near correction-plan projection path plus offline analyzer support for `...-plan.csv`
- [ ] Motor phase tuning Phase 2
  - [x] Reject the host-`dwell()` direct-mode path as too coarse for benefit testing
  - [x] Reject the scheduled-SPI direct-mode path as still audibly stuttering
  - [x] Prove that host flush and queue/load seams are too coarse, while the execution-near seam is diagnostically useful
  - [x] Stabilize the printer-side `H2/H4` execution-near gate as the current acceptance baseline
  - [ ] Define the smallest dedicated phase-stepping executor for one motor on the mainboard MCU
  - [ ] Keep the first executor limited to `stepper_y @ 30 mm/s` with the frozen `H2/H4` working set
  - [ ] Add an explicit rollback/escape path from direct mode back to normal step/dir
- [ ] Motor phase tuning Phase 3
  - [x] Build a baseline-only dedicated executor (src/motor_phase_exec.c)
    - [x] 1024-point cos/sin tables computed at init via Q15 recursive rotation
    - [x] timer ISR advances phase_index, sets pending flag, wakes task
    - [x] DECL_TASK writes 5-byte DIRECT_MODE SPI frame per tick
    - [x] config/start/stop MCU commands
    - [x] DECL_SHUTDOWN marks executors idle
    - [x] Added to src/Makefile under CONFIG_HAVE_GPIO_SPI
  - [x] Host-side MOTOR_PHASE_EXEC_RUN command in motor_phase_calibration.py
    - [x] MotorPhaseExec Python class sets up MCU SPI + executor OID at config time
    - [x] exec_stepper / exec_spi_bus / exec_cs_pin config params in printer.cfg
    - [x] reuses _with_direct_mode + _stage_xy_position for safe entry/exit
    - [x] re-homes XY automatically after run (position unknown after direct mode)
  - [x] Build MCU binary (out_at32f403a/at32f403a.bin, 48 KB)
  - [x] Stage the custom main-MCU bundle into the SoC image so `check-restore`
    no longer reverts the flashed executor MCU back to stock on reboot
  - [ ] Split startup MCU version checks so `mcu0` and `head0..head3` can use
    different expected versions without disabling `check-restore`
  - [ ] Validate clean direct-mode entry/exit and no desync after the baseline run
  - [ ] Assess whether baseline motion is audibly smooth (no stutter)
  - [ ] Only after baseline smoothness is proven, add correction-capable mode

---

## Handoff
- Agent: Codex
- Date: 2026-04-05
- Completed this session:
  - reviewed the newly added baseline-only MCU executor and host wiring
    (`src/motor_phase_exec.c`, `MOTOR_PHASE_EXEC_RUN`, overlay host sync path)
  - corrected stale statements in `motor-phase-tuning.md`
    - `tmc2240.py` already exposes `DIRECT_MODE`, `coil_a`, and `coil_b`
    - build integration uses `src-$(CONFIG_HAVE_GPIO_SPI) += motor_phase_exec.c`
  - harmonized the documented SoC/MCU build and flash flow
    - SoC: `./dev.sh make build PROFILE=extended`
    - MCU: `make CPP=arm-none-eabi-cpp clean && make CPP=arm-none-eabi-cpp`
    - staged MCU artifact: `out_at32f403a/at32f403a.bin`
  - verified current Python-side changes compile cleanly with `py_compile`
  - documented the main runtime risk of the current executor design:
    - the MCU side currently uses a single `pending` flag, so missed service
      windows collapse multiple timer ticks into one SPI update
  - built fresh host and MCU artifacts successfully
    - `firmware/firmware.bin`
    - `tmp/firmware/update.img`
    - `out_at32f403a/at32f403a.bin`
  - cleaned up MCU version metadata for the next flash:
    - removed hostname suffix from `scripts/buildcommands.py`
    - replaced the zero USB product suffix placeholder in the U1 MCU configs
  - found and fixed the reboot revert path for the custom main MCU:
    - `S60klipper` runs `systemUpgrade.sh check-restore` on every start
    - the stock image still shipped `/home/lava/firmware_MCU/VERSION` as
      `20260323110253-51d366c286`, so a manually flashed `localbuild` MCU was
      treated as mismatched and restored back to stock
    - the SoC build now stages the local `out_at32f403a/at32f403a.bin` into
      both `/home/lava/firmware_MCU/at32f403a.bin` and the top-level upgrade
      bundle, and rewrites `VERSION`, `md5sum.txt`, and `MCU_DESC` to
      `19700101000000-localbuild`
  - identified the follow-up reboot delay bug after that fix:
    - `systemUpgrade.sh` still used one global expected version for both the
      custom main MCU and the stock toolhead MCUs
    - after the main MCU started reporting `localbuild`, startup kept trying to
      reconcile the unchanged head MCUs on every boot, causing the long loop-like
      startup delay
    - the proper fix is to keep checks enabled but split the expected versions
      into `VERSION_MAIN` and `VERSION_HEAD`
  - implemented that split-version fix in the image build:
    - `systemUpgrade.sh` now resolves expected MCU versions per board type
    - the SoC image now stages `VERSION_MAIN=19700101000000-localbuild`
    - the SoC image now stages `VERSION_HEAD=20260323110253-51d366c286`
    - rebuilt `tmp/firmware/update.img` and `firmware/firmware.bin`
    - verified the new files directly in `tmp/firmware/rootfs`
  - verified on hardware that the split-version boot fix works:
    - printer now boots normally again
    - `show-status` reports `Main MCU = localbuild`
    - `show-status` reports all head MCUs still on stock `51d366c286`
    - no `skip_checking_mcu` workaround required
  - ran the first baseline executor hardware test:
    - `MOTOR_PHASE_EXEC_RUN` now starts instead of failing on protocol/version mismatch
    - current failure mode is electrical/runtime, not boot/version
    - observed failure:
      - `Unable to obtain 'spi_transfer_response' response`
      - `GSTAT reset=1 uv_cp=1 vm_uvlo=1`
  - captured the pre-failure TMC state:
    - idle `GSTAT=0`
    - `GCONF=0x00000008`
    - `CHOPCONF=... mres=2(64usteps) intpol=1`
    - `DRV_STATUS ... cs_actual=0(Reset?) stst=1`
    - `MSCNT=1022`
  - reduced executor update density on the host side:
    - `MOTOR_PHASE_EXEC_RUN` now accepts `PHASE_STRIDE`
    - default stride is now `16`
    - this keeps checks and current executor architecture intact, but reduces
      SPI update pressure substantially versus one update per step
  - rebuilt fresh SoC artifacts for the stride-based retest:
    - `tmp/firmware/update.img`
    - `firmware/firmware.bin`
- Stopped at:
  - boot/version path is fixed and verified on hardware
  - executor baseline path still fails electrically at current runtime settings
  - the stride-based host retest image is built, but not yet flashed/tested
- Next step:
  1. Flash the latest SoC image for the stride-based executor retest:
     - `scp tmp/firmware/update.img root@<u1-ip>:/tmp/`
     - `ssh root@<u1-ip> /home/lava/bin/systemUpgrade.sh upgrade soc /tmp/update.img`
  2. Run the reduced-rate baseline executor retest:
     - `MOTOR_PHASE_EXEC_RUN STEPPER=stepper_y SPEED=30 DISTANCE=20 COIL_SCALE=40 PHASE_STRIDE=16`
  3. If it still fails, capture immediately:
     - `DUMP_TMC STEPPER=stepper_y REGISTER=GSTAT`
     - `DUMP_TMC STEPPER=stepper_y REGISTER=DRV_STATUS`
     - `DUMP_TMC STEPPER=stepper_y REGISTER=MSCNT`
  4. If the stride-based retest still trips `uv_cp` / `vm_uvlo`, redesign before
     any correction mode:
     - current timer->pending->DECL_TASK SPI path is still too aggressive or
       otherwise not electrically/runtime-safe
- Open blockers:
  - direct-mode enable/disable sequencing is only partially hardened; the first
    entry fix now aligns to current `MSCNT`, but the baseline executor has not
    yet proven it can enter and exit without `GSTAT reset/drv_err/vm_uvlo`
  - current executor coalesces timer ticks behind a single `pending` flag; if
    the SPI task cannot keep up, phase updates are dropped and motion may stutter
  - even with the boot/version path fixed, the current baseline executor can
    still trip TMC undervoltage/reset faults during runtime
- Decisions made this session:
  - baseline-only executor remains the right first runtime-near test scope
  - current `H2/H4` gate remains a runtime-near acceptance metric, not a benefit claim
  - MCU build docs must use the root `make CPP=arm-none-eabi-cpp ...` flow
  - the current executor design's `pending`-bit coalescing behavior is an
    explicit risk to validate, not an implementation detail to ignore
  - MCU dirty-build strings should keep the timestamp but must not include the
    workstation hostname
  - the SoC image must ship the same main-MCU bundle metadata as the manually
    flashed custom MCU, otherwise `systemUpgrade.sh check-restore` reverts the
    executor MCU back to stock on the next Klipper start
  - startup checks must remain active; the correct mixed-version dev setup is
    separate expected versions for main and head MCUs, not `skip_checking_mcu`
  - the current next experiment is to reduce executor SPI update density with
    `PHASE_STRIDE=16` before attempting any deeper MCU-side redesign

## Notes
- Remote Screen in the current Extended Firmware is include-driven and nginx-sidecar-based; it is not just a direct `printer.cfg` modification anymore
- Existing printers keep their current `extended/` files on upgrade; the repo now migrates `extended2.cfg` to `extended.cfg` on first boot if needed
- Camera support currently depends on an external pinned `v4l2-mpp` build
- The imported plan should preserve minimality: only the selected 7 features plus their required support layers belong in scope
- This repo is now the working base for future U1 development, including selective ports from mainline Klipper, as long as U1-specific integration remains reviewable and isolated
- New feature tracks should distinguish verified repo facts from proposed design. `motor-phase-tuning.md` is currently a feasibility plan, not an implementation-ready task list.
- Mainline Klipper reference for the lookahead flush change: `16fc46fe5ff0dbbc5188ee6a7829eee5976c1eb9` (`toolhead: Reduce LOOKAHEAD_FLUSH_TIME to 0.150 seconds`, 2025-09-30)
- Official SSH remains enabled through the stock Snapmaker UI path; this repo only persists Dropbear host keys into `/oem/dropbear` so the client host key stays stable across reboots
  - Implementation detail: boot now rewires `/etc/dropbear` to `/oem/dropbear` before any SSH startup path runs, instead of patching Snapmaker's SSH activation flow
  - The boot hook must be shipped as `S49persist-dropbear-hostkeys.sh` so BusyBox `rcS` sources it even if overlay file modes are not executable

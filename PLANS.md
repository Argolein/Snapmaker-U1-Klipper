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

### 5. Verified overlay order for `PROFILE=extended`

The current build uses this order:

1. `overlays/common/04-nginx-fluidd.d/`
2. `overlays/common/05-persist-dhcp/`
3. `overlays/common/06-persist-eth0-mac/`
4. `overlays/firmware-extended/01-repo-host-sync/`
5. `overlays/firmware-extended/02-firmware-config/`
6. `overlays/firmware-extended/14-patch-firmware-files/`
7. `overlays/firmware-extended/20-disable-ipv6/`
8. `overlays/firmware-extended/21-add-curl/`
9. `overlays/firmware-extended/22-patch-moonraker/`
10. `overlays/firmware-extended/23-usb-ethernet/`
11. `overlays/firmware-extended/33-feature-timelapse-stub/`
12. `overlays/firmware-extended/60-app-camera/`
13. `overlays/firmware-extended/61-app-remote-screen/`

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
  - [x] Full build/repack verification complete with `./dev.sh make build PROFILE=extended`
- [x] Phase 6: end-to-end validation
  - [x] Final firmware image builds from this repo: `firmware/firmware.bin`
  - [x] Rebuilt rootfs inspected in `tmp/firmware/rootfs`
  - [x] Flash to printer
  - [x] Verify selected features on hardware
  - [x] Document final build/flash workflow

---

## Handoff
- Agent: Codex
- Date: 2026-04-04
- Completed this session:
  - Validated `PLANS.md` against the current repo and `SnapmakerU1-Extended-Firmware`
  - Corrected the false wildcard-include assumption
  - Confirmed the image-builder infrastructure listed in the plan exists in the Extended Firmware repo
  - Confirmed current feature dependencies and support overlays for Remote Screen, Camera, and USB Ethernet
  - Corrected the camera assumption from “unknown binary source” to “external pinned source build”
  - Corrected the curl SHA256 and updated the architecture to match the user's stated target
  - Imported the image-builder scripts, helper scripts, tool sources, Dockerfile, `vars.mk`, and `dev.sh`
  - Imported `deps/screen-apps/`
  - Integrated image-builder targets into the root `Makefile`
  - Isolated pure image-builder targets from the MCU build graph in the root `Makefile`
  - Added the baseline `overlays/` structure
  - Imported the minimal support overlays for include config, nginx `fluidd.d`, DHCP persistence, MAC persistence, and USB NIC firmware files
  - Imported the low-risk feature overlays for extended includes, IPv6 disable, and curl
  - Imported the medium-risk feature overlays for OEM disk usage and USB Ethernet
  - Verified overlay ordering from the root builder with `make overlays PROFILE=extended`
  - Verified tool dispatch from the root builder with `make -n tools`
  - Verified the imported builder in Docker with `./dev.sh make tools`, `./dev.sh make firmware`, and `./dev.sh make extract PROFILE=extended`
  - Imported the missing Phase 5 overlays for Remote Screen and Camera
  - Added a repo-owned host-sync overlay for `lava/*.cfg`
  - Corrected the imported Remote Screen HTML install path to `/usr/local/share/fb-http/html`
  - Completed a full end-to-end Docker build to `firmware/firmware.bin`
  - Verified the rebuilt rootfs contains the selected feature files and config hooks
  - Removed the stale USB-Ethernet `dhcpcd.conf` patch after confirming stock firmware `1.2.0.106` already enables `eth0`
  - Documented the current build outputs and flash commands in `README.md`
  - Documented the exact verified image build workflow and overlay order in this `PLANS.md`
  - Documented the recommended first hardware flash procedure in this `PLANS.md`
  - Imported the timelapse compatibility stub from Extended Firmware for future builds
  - Rebuilt `firmware/firmware.bin` after changing the Remote Screen defaults and cleaning up the `timelapse` stub
  - Renamed the runtime config path from `extended2.cfg` to `extended.cfg` across the imported feature set
  - Added first-boot migration from `extended2.cfg` to `extended.cfg` for existing printers and rebuilt the firmware image
  - Flashed the resulting firmware to a real U1 and validated the selected features on hardware
- Stopped at:
  - End-to-end implementation, build, flash, and feature validation are complete
- Next step:
  - Use this repo as the source of truth for future feature work and regression checks against new Snapmaker base firmware releases
- Open blockers:
  - none
- Decisions made this session:
  - this repo must own the resulting image build, not only patch a stock firmware image
  - minimal support overlays are allowed when required by the selected 7 features
  - USB NIC firmware blobs from `ccde0b8` are in scope

---

## Notes
- Remote Screen in the current Extended Firmware is include-driven and nginx-sidecar-based; it is not just a direct `printer.cfg` modification anymore
- Existing printers keep their current `extended/` files on upgrade; the repo now migrates `extended2.cfg` to `extended.cfg` on first boot if needed
- Camera support currently depends on an external pinned `v4l2-mpp` build
- The imported plan should preserve minimality: only the selected 7 features plus their required support layers belong in scope
- This repo is now the working base for future U1 development, including selective ports from mainline Klipper, as long as U1-specific integration remains reviewable and isolated

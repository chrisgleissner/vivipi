# Multi-Device Support — ePaper Stabilization Handover Prompt

You are taking over a narrowly scoped stabilization task on `feat/multi-device-support`.

## Goal

Recover the **Waveshare Pico-ePaper-2.13-B-V4** so it renders the ViviPi probe overview correctly again, **without regressing the OLED Pico**.

The OLED Pico is currently fine. The ePaper Pico currently shows **random pixels** instead of readable probes.

## Concrete captured hardware findings

Do not treat the current ePaper failure as hypothetical anymore. It has been directly captured.

Artifacts already captured in this repo:

- `artifacts/pixel4/vivipi-epaper-proof.png`
- `artifacts/pixel4/vivipi-epaper-proof-center.png`

What the capture shows:

1. the ePaper panel is **not blank**
2. the panel is **not showing a partial readable frame**
3. the active display area is filled with dense **red / black / white salt-and-pepper noise**
4. the noise is contained **inside the visible ePaper panel area**, surrounded by the normal white bezel/frame
5. this means the bug is very likely in the **panel/controller upload path**, not in camera framing, not in check sorting, and not in ordinary text layout

Interpretation:

- This is now a **low-level ePaper encoding / init / plane / refresh problem until proven otherwise**.
- Do **not** spend time reworking overview-row ordering, selection, or heartbeat reservation unless the captured panel output changes away from full-panel corruption.

## Strong convergence requirements

1. **Do not restart broad exploration.**
2. **Do not touch OLED logic unless you have proof it is required.**
3. Treat this as an **ePaper-backend-only** stabilization unless hard evidence proves otherwise.
4. Prefer the **repo’s already-working Waveshare driver patterns** over inventing new rendering or transport logic.
5. Do not keep trying ad hoc on-device redraw experiments unless each one is tied to a specific code difference you are validating.
6. **Use the Pixel 4 camera autonomously for hardware proof. Do not depend on manual operator confirmation if the Pixel 4 path is available.**

## Hardware-proof rule

The Pixel 4 camera is pointed at the device and should be treated as the primary hardware-facing verification path.

Use it autonomously:

1. Keep the Pixel 4 camera app active.
2. Capture the phone screen over ADB.
3. Pull the image back to the host.
4. Judge the ePaper output from the captured image before deciding whether a change worked.

Known repo-backed observation facts:

- Pixel 4 serial: `9B081FFAZ001WX`
- documented observation path: `adb shell screencap -p ...` plus `adb pull ...`
- camera app previously confirmed active via `dumpsys window` with `org.lineageos.aperture/.CameraLauncher`
- the checked-in local config already expects the Pixel 4 ADB path on this machine
- a live capture was successfully pulled in this session, so assume the Pixel 4 observation path is working unless `adb` proves otherwise

Do not stop at:

- a successful deploy
- absence of exceptions
- serial logs alone

You are not done until the **captured Pixel 4 image** shows readable ViviPi output on the target display.

## Default autonomous verification loop

Use a loop like this after each meaningful ePaper deploy:

1. deploy to `epaper-lab`
2. wait for the panel refresh to settle
3. capture a fresh Pixel 4 camera-view screenshot
4. inspect the image for:
   - readable probe names
   - stable row layout
   - expected bottom progress pixel area
   - absence of random full-panel noise
5. only then decide whether the last code change helped

If the Pixel 4 camera app is not active, recover it autonomously before asking for help:

1. inspect the focused window with `adb -s 9B081FFAZ001WX shell dumpsys window`
2. if needed, relaunch the camera app
3. then capture again

## Concrete capture path

Prefer direct ADB capture rather than interactive scrcpy:

```bash
adb -s 9B081FFAZ001WX shell screencap -p /sdcard/Download/vivipi-epaper-proof.png
adb -s 9B081FFAZ001WX pull /sdcard/Download/vivipi-epaper-proof.png artifacts/pixel4/
```

If you need to confirm the camera preview is really active:

```bash
adb -s 9B081FFAZ001WX shell dumpsys window | grep -i aperture
```

Store pulled captures under `artifacts/` or another non-committed path and compare before/after images as needed.

## What is already known

### OLED status

- The OLED regression was real and was fixed by replacing MicroPython-incompatible `casefold()` sorting with a safe fold helper.
- The OLED Pico is now confirmed to render all probes correctly again.
- Do **not** revisit OLED sorting/render/runtime paths unless a new regression is observed.
- Passive serial/log evidence from the OLED board still shows a healthy runtime path, including:
  - `app ready`
  - `network ready`
  - `startup tick`
  - live probe activity such as `probe-start` / `dns-start` / `dns-result`
- Use the OLED serial path as the non-visual regression check:
  - `/dev/serial/by-id/usb-MicroPython_Board_in_FS_mode_740c0800366c92bb-if00`

### ePaper config/runtime status

These parts are already in the correct area and are **not the primary suspect anymore**:

- `config/build-deploy.multi.local.yaml`
  - `epaper-lab.device.display.type = waveshare-pico-epaper-2.13-b-v4`
  - `rotation = 180`
  - `display.liveness.bottom_heartbeat.enabled = true`
- `firmware/eink_runtime.py`
  - bottom indicator reservation exists
  - bottom heartbeat pixels are emitted
  - probe rows are alphabetically sorted with the MicroPython-safe fold helper
- The ePaper device boots the expected runtime path and identifies itself as:
  - `waveshare-pico-epaper-2.13-b-v4`
  - rotation `180`
  - bottom heartbeat `True`

### On-device deploy fact

Do **not** assume the device is running stale code.

This was explicitly verified:

- `mpremote fs cat displays/waveshare_epaper.py` on the ePaper Pico matched the repo copy.
- So the ePaper backend file **is** being updated on-device.

## What has already been tried

The ePaper panel still appeared as **random pixels** after each of the following:

1. A transport-order rewrite in `firmware/displays/waveshare_epaper.py`
2. Reverting that transport-order rewrite
3. Forcing the old generic `render_to_surface()` path on-device
4. Forcing a full legacy raw upload path on-device
5. Forcing explicit SPI setup in an on-device one-off test
6. Forcing sampled glyph rasterization in an on-device one-off test
7. Aligning low-level driver behavior toward the stable Waveshare pattern:
   - explicit SPI constructor wiring
   - `cs(1)` before command/data writes
   - 2.13-v4-style init values (`0x11 -> 0x03`, `0x21 -> 0x00, 0x80`)
   - sleep after refresh

Important sequencing fact:

- The current captured random-pixel proof reflects the **currently deployed board state before validating/deploying the latest removal of the special framebuf text path**.
- So the next step is still to validate and deploy that latest `waveshare_epaper.py` worktree state, then compare a new Pixel 4 capture against this saved baseline.

## Repo-backed conclusion so far

The remaining fault domain is overwhelmingly concentrated in:

- `firmware/displays/waveshare_epaper.py`

Not in:

- probe sorting
- eInk runtime summary-frame building
- multi-device config expansion
- deploy artifact staging
- OLED runtime code

More precise interpretation after the Pixel 4 proof:

- the failure is almost certainly **below the semantic frame-building layer**
- the highest-probability fault area is now:
  - panel init values
  - RAM entry/window/cursor programming
  - black/red plane upload semantics
  - refresh / sleep sequencing
  - any backend-specific buffer interpretation in `waveshare_epaper.py`

## Important repo facts to use

Read these files first and use them as the stability reference:

1. `firmware/displays/waveshare_epaper_mono.py`
2. `firmware/displays/waveshare_epaper_tricolor.py`
3. `docs/research/multi-device-support/spec.md`
   - especially the note that the 2.13-B panel should do a full refresh and then sleep

The 2.13-B-V4 backend is currently the outlier. The stable mono/tricolor drivers use:

- explicit SPI wiring
- straightforward command/data flow
- shared `render_to_surface()` rendering
- full refresh
- sleep after refresh

Additional convergence hint:

- Because the captured panel output is full-panel corruption rather than malformed readable glyphs, prefer comparing **controller protocol and plane semantics** before revisiting font rasterization.

## Current worktree state

There are already-uncommitted stabilization edits from this session in multiple files.

The most relevant still-open change is:

- `firmware/displays/waveshare_epaper.py`
  - **latest unvalidated edit**: `draw_frame()` and `show_boot_logo()` were changed to always use the shared `render_to_surface()` / `render_boot_logo_to_surface()` path instead of the special framebuf-text fast path.
  - This change was made because the 2.13-B-V4 backend is the only Waveshare driver here that still had a special text-render path.
  - **This latest change has not yet been run through CI or deployed after the user asked for handover.**

Related test file:

- `tests/unit/firmware/test_display.py`

## High-probability next move

Before doing anything else:

1. Validate the current worktree.
2. Deploy the current `waveshare_epaper.py` state to `epaper-lab`.
3. Use the Pixel 4 camera path to capture proof of the panel result.
4. Compare that new capture against the saved random-noise baseline already in `artifacts/pixel4/`.

Why this is the best next move:

- It is the first repo-aligned change that combines:
  - the low-level driver alignment work
  - **and** the removal of the special framebuf text path
- Earlier generic-render tests were done **before** the low-level driver alignment, so they do not fully cover the current state.

## Exact next steps

1. Run:

   ```bash
   ./build ci
   ```

2. Deploy only the ePaper board:

   ```bash
   ./.venv/bin/python -m vivipi.tooling.build_deploy deploy-firmware \
     --config config/build-deploy.multi.local.yaml \
     --output-dir artifacts/release \
     --device epaper-lab
   ```

3. Capture a fresh Pixel 4 proof image of the panel.

4. If the capture now shows readable probes, preserve that exact backend direction and stop broad investigation.

5. If the capture still shows random pixels:
   - keep the scope inside `firmware/displays/waveshare_epaper.py`
   - treat the problem as a controller/upload-path bug first
   - compare it line-by-line against the stable patterns in:
     - `waveshare_epaper_mono.py`
     - `waveshare_epaper_tricolor.py`
   - prioritize these comparisons:
     - SPI constructor and pin binding
     - command/data toggling
     - `0x44`, `0x45`, `0x4E`, `0x4F` window/cursor programming
     - `0x24` / `0x26` plane writes
     - refresh command sequence
     - sleep timing and reset timing
   - do **not** go back to changing:
     - `firmware/eink_runtime.py`
     - `firmware/runtime.py`
     - `src/vivipi/core/state.py`
     - multi-device config logic

6. If you touch any shared code or anything that could affect OLED behavior, re-check the OLED via passive serial logs rather than visual output.

## What not to do next

Do **not**:

1. rework sorting again
2. rework bottom heartbeat logic again
3. re-open OLED fixes
4. keep trying one-off `mpremote exec` redraw variants without tying them to a concrete code delta
5. spread the investigation beyond `waveshare_epaper.py` unless you have direct evidence
6. replace hardware proof with manual textual reports if the Pixel 4 capture path is available
7. interpret this as a text-layout problem while the Pixel 4 image still shows full-panel random noise

## Desired completion state

You are done only when:

1. the **captured Pixel 4 image** shows the ePaper Pico rendering readable ViviPi probes again,
2. the OLED Pico still renders correctly, with serial/log evidence if the panel is not visible,
3. `./build ci` still passes.

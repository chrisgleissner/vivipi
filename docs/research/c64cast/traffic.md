# c64cast U64 Socket-DMA Traffic

## Scope

This report covers the U64 socket-DMA path.

Every C64 memory write becomes a `CMD_DMAWRITE = $FF06` packet:

```text
<HH opcode,len> + <H addr_le> + data
```

The packet bytes are:

```text
06 FF (2 + len(data))_le addr_lo addr_hi data...
```

REU writes use `CMD_REUWRITE = $FF07` with this payload:

```text
<reu_offset_24_le> + data
```

Relevant code:

- `c64cast/socket_dma.py:240`
- `c64cast/api.py:1141`

## Key Point

`per video frame` maps to display-mode `push()` calls.

Audio is an independent stream of DMA chunks or REU preloads plus IRQ/NMI setup. It is not one audio call per video frame.

## Per Rendered Video Frame

A `VideoScene` chooses one decoded image by audio clock, skips repeated ndarray frames, then calls `display_mode.push(...)` through `_render_with_overlays()`. Buffer overlays are already folded into these payloads, so they do not add separate writes.

Relevant code:

- `c64cast/scenes.py:1163`
- `c64cast/scenes.py:336`

`write_region()` is data-dependent. The first frame, or a length change, is a full write. Later frames may be skipped, emitted as one dirty span, emitted as 256-byte dirty slabs, or emitted as a full region.

The physical addresses below are exact bases. The actual emitted sub-address can be `base + dirty_offset`.

Relevant code:

- `c64cast/backend.py:476`

| Display mode | Per-frame calls | Payload |
| --- | --- | --- |
| `petscii` | `write_region($0400, screen)`, `write_region($D800, color)` | `screen`: 1000 PETSCII bytes. `color`: 1000 color-RAM bytes. Optional REU mode replaces the screen write; color stays DMA. |
| `petscii` with `use_reu_staged` | `REUWRITE $E00000, screen[1000]`; `DMAWRITE $DF02` 2 bytes dest; `$DF04` 3 bytes REU src; `$DF07` 2 bytes length; `$DF01` 1 byte `$91`; then `write_region($D800, color)` | REU stages screen, REC copies to `$0400`. See `c64cast/modes.py:1277`. |
| `mcm` | Optional `write_regs($D020, bg0, bg0, bg1, bg2)` when bg set changes; `write_region($0400, screen)`, `write_region($D800, color)` | `screen`: 1000 packed 2x2 multicolor chars. `color`: 1000 bytes with high bit set. See `c64cast/modes.py:1892`. |
| `hires` single-buffer | Optional `write_regs($D020, bg, bg)`; `write_region($2000, bitmap)`, `write_region($0400, screen)` | `bitmap`: 8000 bytes. `screen`: 1000 fg/bg nibble bytes. See `c64cast/modes.py:2071`. |
| `hires` host-DMA double-buffer | Optional `$D020`; `write_region($2000/$A000, bitmap)`, `write_region($0400/$8400, screen)`, `write_memory_file($C700, [bg, dd00, 01])` | Alternates off-screen bank. Tracker is 3 bytes. IRQ flips `$DD00` at vblank. |
| `hires` REU staged | Optional `$D020`; `REUWRITE $E10000, bitmap[8000]`; `REUWRITE $E12000, screen[1000]`; `write_memory_file($C700, tracker[16])` | Tracker packs REC regs for bitmap and screen, pending `$DD00 $97` or `$95`, ready `01`. See `c64cast/modes.py:1139`. |
| `mhires` single-buffer | Optional `write_regs($D021, bg0)`; `write_region($0400, screen)`, `write_region($D800, color)`, `write_region($2000, bitmap)` | `bitmap`: 8000 multicolor bitmap bytes. `screen`: 1000 c1/c2 bytes. `color`: 1000 c3 bytes. See `c64cast/modes.py:2380`. |
| `mhires` host-DMA double-buffer | `write_region($2000/$A000, bitmap)`, `write_region($0400/$8400, screen)`, `write_region($D800, color)`, `write_memory_file($C700, [bg0, dd00, 01])` | Bitmap/screen alternate bank. Color RAM is shared. |
| `mhires` REU staged | `REUWRITE $E10000, bitmap[8000]`; `REUWRITE $E12000, screen[1000]`; `REUWRITE $E13000, color[1000]`; `write_memory_file($C700, tracker[24])` | Tracker packs bitmap/screen/color REC regs, `bg0`, `$DD00 $97/$95`, ready `01`. See `c64cast/modes.py:1196`. |

## Audio Calls

Audio is not one call per video frame. Video audio is demuxed independently. The video frame clock follows `AudioStreamer.position_seconds()`.

## Default Host-DMA Audio

Setup writes:

- `write_memory_file($C020, NMI routine[32])`
- `write_memory_file($4000, neutral[8192])`
- `write_regs($DD0D, $7F, $00)`
- `write_regs($0318, $20, $C0)`
- After prebuffer: `write_regs($DD04, latch_lo, latch_hi)` and `write_regs($DD0D, $81, $11)`

Steady-state writes:

- Every audio chunk, `write_memory_file(write_addr, dac_bytes)`.
- `write_addr` starts at `$4000`, advances by up to 1024 bytes, and wraps before `$6000`.
- Payload is 4-bit SID volume DAC codes, one byte per sample, consumed by the NMI handler writing `$D418`.
- If host-DMA servo is enabled, each chunk also performs REST `read_memory($C025, 2)` for the NMI read pointer. That read is not socket DMA.

Relevant code:

- `c64cast/audio.py:1286`
- `c64cast/audio.py:1490`
- `c64cast/audio.py:1570`

## REU-Staged Video Audio

Before playback, the full encoded audio is uploaded by `REUWRITE $000000 + off, audio_4bit_slice` in 32 KiB slices, plus a neutral EOF pad.

Other writes:

- Ring prefill: `write_memory_file($4000, first_8192_audio_bytes)`
- Pump code/control writes:
  - Handler at `$C100`
  - Optional tracker at `$C200`
  - REC regs `$DF02-$DF0A`
  - CIA1 latch `$DC04/$DC05`
  - NMI timer `$DD04/$DD0D`
  - Optional IRQ vector `$0314 -> $C100`
- No per-video-frame host audio payload writes. C64 IRQ/REC pumps REU audio into `$4000-$5FFF`.

Relevant code:

- `c64cast/audio.py:2212`

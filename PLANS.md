# Plans

## Phase 1: Scaffold baseline

- Establish repository layout
- Define pure-core state and rendering modules
- Add host-side default ADB service
- Add tests, CI, coverage upload, and release automation

## Phase 2: Hardware runtime

- Implement SH1107 SPI driver for MicroPython
- Implement button GPIO polling and debounce wiring
- Implement Pico 2W Wi-Fi bootstrap and transport client
- Implement event-driven runtime loop on device

## Phase 3: Full product behavior

- Implement direct `PING` and `REST` execution layers
- Implement diagnostics view population
- Harden deployment ergonomics and firmware flashing flow
- Add hardware-in-the-loop verification

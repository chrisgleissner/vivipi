# U64 Connection Test Expansion

## 1. Overview

This audit reviewed the current host-side probe script at `scripts/u64_connection_test.py` and performed a repository-wide scan of the bundled Ultimate 64 / 1541ultimate firmware tree under `1541ultimate/` for inbound network surfaces.

The repository scan covered the required classes of evidence:

- socket listeners and accepts: `socket(`, `bind(`, `listen(`, `accept(`, `recv(`, `recvfrom(`, `send(`, `sendto(`
- stack-level network handling: lwIP ICMP enablement and ICMP input path
- application services: HTTP, FTP, Telnet, raw socket control, UDP identify, modem listener
- route-level HTTP API exposure via `API_CALL(...)`

### Conclusion

`scripts/u64_connection_test.py` does **not** provide complete coverage of the device's inbound network surface.

It provides solid coverage for:

- ICMP echo reachability
- the primary HTTP daemon on TCP port 80, but only for a selected subset of API routes
- FTP control plus passive data paths
- the Telnet remote menu service
- the audio/video stream-control subset of the raw socket service on TCP port 64, but only when stream monitoring is enabled

It does **not** cover:

- the UDP identify responder on port 64
- the DMA-capable raw socket command service on TCP port 64 as a first-class probe
- password-protected HTTP, Telnet, FTP, and raw-socket deployments through one shared device network password
- the optional ACIA modem TCP listener
- several HTTP route families that are externally reachable but intentionally state-changing

Important clarification from the firmware source:

- TCP port `64` is the DMA-capable command ingress implemented by `SocketDMA::dmaThread` in `1541ultimate/software/network/socket_dma.cc:362-476`
- UDP port `64` is **not** a DMA transport; it is the separate identify responder implemented by `SocketDMA::identThread` in `1541ultimate/software/network/socket_dma.cc:478-599`
- the current socket-64 firmware command set includes DMA-style write/load/execute operations, but it does **not** expose a general raw-memory-read opcode on that TCP interface
- generic C64 memory read/write coverage already exists through HTTP `/machine/readmem` and `/machine/writemem`, which call `C64_DMA_RAW_READ` and `C64_DMA_RAW_WRITE` in `1541ultimate/software/api/route_machine.cc:70-203`
- bundled helper scripts such as `1541ultimate/python/sock.py` are not the source of truth when they diverge from `socket_dma.cc`

## 2. Definition Of Network Surface

For this audit, the inbound network surface includes any production firmware path that can receive externally originated traffic over IP or an adjacent network transport.

Included:

- ICMP echo handling in the lwIP stack
- TCP listeners started by production init paths
- UDP listeners started by production init paths
- HTTP API route families reachable behind the HTTP daemon
- raw command channels reachable over TCP/UDP
- the ACIA modem listener when enabled by runtime configuration

Excluded from the production surface inventory:

- outbound-only clients such as remote syslog and SNTP
- outbound UDP stream transmission itself
- standalone examples, contrib demos, and tests that are present in-tree but not wired into production startup

## 3. Summary Of Existing Script Coverage

Sections 3 through 6 describe the pre-expansion baseline observed during the audit. Section 7 and later define the corrected target scope for the extension work.

### Default protocols and ports

The current script exposes four named probes by default:

- `ping`
- `http`
- `ftp`
- `telnet`

Evidence:

- `scripts/u64_connection_test.py:36-40` defines default host and ports: HTTP `80`, Telnet `23`, FTP `21`
- `scripts/u64_connection_test.py:46` defines `DEFAULT_PROBES = ("ping", "http", "ftp", "telnet")`
- `scripts/u64_connection_test.py:56-57` limits stream choices to `audio` and `video`

### Profiles and stream behavior

- The soak/default profile enables audio and video stream monitoring in addition to the named probes.
- The stress profile disables stream monitoring.

Evidence:

- `scripts/u64_connection_test.py:416-438` defines the soak profile with readwrite HTTP/FTP/Telnet plus default streams
- `scripts/u64_connection_test.py:439-458` defines the stress profile without streams
- `scripts/u64_connection_test.py:701-739` starts `u64_stream.StreamMonitor` when `config.streams` is non-empty

### Protocol behavior actually exercised

| Probe path | Transport | Default port | Script behavior | Evidence |
| --- | --- | --- | --- | --- |
| `ping` | ICMP echo | n/a | Executes one OS `ping` request and requires a reply | `scripts/u64_ping.py:10-25` |
| `http` | TCP | 80 | `GET` plus extended read/readwrite route operations | `scripts/u64_http.py:287-318`, `scripts/u64_http.py:321-351` |
| `ftp` | TCP | 21 + passive data ports | Connects, logs in, enables PASV, then performs read/write file operations | `scripts/u64_ftp.py:309-315`, `scripts/u64_ftp.py:719-745`, `scripts/u64_ftp.py:747-839` |
| `telnet` | TCP | 23 | Connects, reads banner/menu text, drives the VT100 menu, and optionally changes audio mixer state | `scripts/u64_telnet.py:198`, `scripts/u64_telnet.py:794-824`, `scripts/u64_telnet.py:825-903` |
| stream monitor | TCP control + UDP receive | TCP 64 control, ephemeral local UDP receive ports | Sends raw socket stream enable/disable commands for audio/video and validates received UDP packet structure | `scripts/u64_stream.py:12`, `scripts/u64_stream.py:148-149`, `scripts/u64_stream.py:402-446` |

### Assumptions and failure behavior

1. HTTP assumes the device network password is empty.

   Firmware-side HTTP routes require the `X-Password` header when `CFG_NETWORK_PASSWORD` is non-empty (`1541ultimate/software/api/routes.h:304-313`). The host HTTP probe never sends that header; it only sends `Connection: close` (`scripts/u64_http.py:50-55`, `scripts/u64_http.py:341-350`).

2. Telnet assumes the device network password is empty.

   Firmware-side Telnet prompts for `Password:` and gates access on the same network password (`1541ultimate/software/network/socket_gui.cc:33-104`). At audit time, the host script exposed no Telnet or general network password option; the only credential CLI option in the main script was `--ftp-pass` (`scripts/u64_connection_test.py:42`, `scripts/u64_connection_test.py:357`, plus the absence of any Telnet password plumbing in `scripts/u64_telnet.py`).

3. Raw socket stream control also assumes the device network password is empty.

   The raw socket service rejects all commands except `SOCKET_CMD_AUTHENTICATE` until authenticated (`1541ultimate/software/network/socket_dma.cc:90-106`). The stream monitor sends `0xFF20`/`0xFF21`/`0xFF30`/`0xFF31` commands directly with no authentication step (`scripts/u64_stream.py:141-149`, `scripts/u64_stream.py:433-446`).

4. FTP is protected by the same shared device network password, but the host-side model currently treats it as an FTP-specific credential.

   The firmware FTP daemon checks `CFG_NETWORK_PASSWORD` in `cmd_pass()` (`1541ultimate/software/network/ftpd.cc:392-402`). At audit time, the host-side shape exposed `--ftp-user` / `--ftp-pass` as if FTP owned a distinct password path (`scripts/u64_connection_test.py:356-360`, `scripts/u64_ftp.py:309-315`).

5. Failure behavior is fast and protocol-local.

   - ICMP: process timeout `4s`, ping wait `2s` (`scripts/u64_ping.py:13-18`)
   - HTTP: connection timeout `3s` for route operations and `8s` for simple GET (`scripts/u64_http.py:50`, `scripts/u64_http.py:341`)
   - FTP: connect timeout `3s` for normal operations and `8s` for some standalone modes (`scripts/u64_ftp.py:309`, `scripts/u64_ftp.py:817`, `scripts/u64_ftp.py:846`, `scripts/u64_ftp.py:872`, `scripts/u64_ftp.py:895`)
   - Telnet: connect timeout `2s`, idle timeout `0.12s` (`scripts/u64_telnet.py:18-21`, `scripts/u64_telnet.py:198`)
   - Streams: control connect timeout `2s`, startup grace `2.0s`, packet stall timeout `1.0s` (`scripts/u64_stream.py:12-15`, `scripts/u64_stream.py:56`, `scripts/u64_stream.py:149`, `scripts/u64_stream.py:169-205`)

## 4. Full Network Surface Inventory

This section lists production inbound interfaces only.

| Surface | Protocol | Port | Purpose | Trigger / enablement | Implementation evidence | Externally reachable |
| --- | --- | --- | --- | --- | --- | --- |
| ICMP echo | ICMP | n/a | Replies to ping requests at the IP stack level | Enabled whenever lwIP ICMP is enabled and the interface has IP reachability | `1541ultimate/software/network/config/lwipopts.h:409`; `1541ultimate/software/lwip/src/core/ipv4/ip4.c:749`; `1541ultimate/software/lwip/src/core/ipv4/icmp.c:80-254` | Yes |
| HTTP daemon | TCP | 80 | Web remote control API | Enabled by `CFG_NETWORK_HTTP_SERVICE`, default `1` | `1541ultimate/software/network/network_config.cc:25`; `1541ultimate/software/network/httpd.cc:29-34`; `1541ultimate/software/httpd/FreeRTOS/lib/server.h:11` | Yes |
| HTTP API: base info | TCP / HTTP | 80 | `/v1/version`, `/v1/info` | Through HTTP daemon | `1541ultimate/software/api/routes.cc:113-134` | Yes |
| HTTP API: configs | TCP / HTTP | 80 | Query/update config categories and values; load/save/reset to flash | Through HTTP daemon | `1541ultimate/software/api/route_configs.cc:163-339` | Yes |
| HTTP API: drives | TCP / HTTP | 80 | Drive state, mount, remove, power, ROM, mode | Through HTTP daemon | `1541ultimate/software/api/route_drives.cc:33-162` | Yes |
| HTTP API: files | TCP / HTTP | 80 | File info and image creation helpers | Through HTTP daemon | `1541ultimate/software/api/route_files.cc:8-156` | Yes |
| HTTP API: machine | TCP / HTTP | 80 | Memory read/write plus lifecycle and debug operations | Through HTTP daemon | `1541ultimate/software/api/route_machine.cc:22-297` | Yes |
| HTTP API: runners | TCP / HTTP | 80 | SID/PRG/CRT/MOD launch helpers | Through HTTP daemon | `1541ultimate/software/api/route_runners.cc:13-125` | Yes |
| HTTP API: streams | TCP / HTTP | 80 | Start/stop video/audio/debug streams | Through HTTP daemon | `1541ultimate/software/api/route_streams.cc:17-65` | Yes |
| FTP control | TCP | 21 | FTP command channel | Enabled by `CFG_NETWORK_FTP_SERVICE`, default `1` | `1541ultimate/software/network/network_config.cc:24`; `1541ultimate/software/network/ftpd.cc:191-238` | Yes |
| FTP passive data | TCP | 51000-60999 | Passive data sockets for LIST/NLST/RETR/STOR/MLSD | Opened on demand by FTP `PASV` | `1541ultimate/software/network/ftpd.cc:246`; `1541ultimate/software/network/ftpd.cc:622-627`; `1541ultimate/software/network/ftpd.cc:980-1010` | Yes |
| Telnet remote menu | TCP | 23 | VT100-style remote UI | Enabled by `CFG_NETWORK_TELNET_SERVICE`, default `1` | `1541ultimate/software/network/network_config.cc:23`; `1541ultimate/software/network/socket_gui.cc:27`; `1541ultimate/software/network/socket_gui.cc:179-217` | Yes |
| Raw socket command service | TCP | 64 | DMA-capable Ultimate command ingress for load/write/control/stream/debug/flash operations | Enabled by `CFG_NETWORK_ULTIMATE_DMA_SERVICE`, default `1` | `1541ultimate/software/network/network_config.cc:22`; `1541ultimate/software/network/socket_dma.cc:40-56`; `1541ultimate/software/network/socket_dma.cc:90-106`; `1541ultimate/software/network/socket_dma.cc:364-475` | Yes |
| Ultimate identify responder | UDP | 64 | Discovery / identification replies (plain text or JSON) | Enabled by `CFG_NETWORK_ULTIMATE_IDENT_SERVICE`, default `1` | `1541ultimate/software/network/network_config.cc:21`; `1541ultimate/software/network/socket_dma.cc:480-599` | Yes |
| ACIA modem listener | TCP | Configurable, default `3000` | Bridges external TCP connections into the modem emulation path | Started when ACIA capability exists; listens only when modem config port is > 0 and ACIA mapping is enabled | `1541ultimate/software/io/acia/modem.cc:71`; `1541ultimate/software/io/acia/modem.cc:863-890`; `1541ultimate/software/io/acia/modem.cc:921-925`; `1541ultimate/software/io/acia/listener_socket.cc:63-84` | Conditionally yes |

## 5. Coverage Matrix

| Surface | Protocol | Port | Covered by script | Evidence | Notes |
| --- | --- | --- | --- | --- | --- |
| ICMP echo | ICMP | n/a | Yes | Host probe: `scripts/u64_ping.py:10-25`; firmware stack: `1541ultimate/software/network/config/lwipopts.h:409`, `1541ultimate/software/lwip/src/core/ipv4/ip4.c:749`, `1541ultimate/software/lwip/src/core/ipv4/icmp.c:80-254` | Stack-level coverage only, but sufficient for reachability |
| HTTP base info (`/v1/version`, `/v1/info`) | TCP / HTTP | 80 | Yes | Host ops include `/v1/version` and `/v1/info`: `scripts/u64_http.py:287-318`; routes: `1541ultimate/software/api/routes.cc:113-134` | Fails on secured devices because no `X-Password` header |
| HTTP configs query/update | TCP / HTTP | 80 | Partial | Host ops hit `/v1/configs`, `/v1/configs/Audio Mixer`, and `PUT` to `Vol UltiSid 1`: `scripts/u64_http.py:287-318`; routes: `1541ultimate/software/api/route_configs.cc:163-339` | Script covers read and one live config write, but not POST bulk update, `load_from_flash`, `save_to_flash`, or `reset_to_default` |
| HTTP drives read path | TCP / HTTP | 80 | Yes | Host op `get_drives`: `scripts/u64_http.py:287-318`; route: `1541ultimate/software/api/route_drives.cc:33-37` | Only the read/list surface is covered |
| HTTP files read path | TCP / HTTP | 80 | Yes | Host op `get_files_temp`: `scripts/u64_http.py:287-318`; route: `1541ultimate/software/api/route_files.cc:8-24` | Script intentionally tolerates `404` for missing `/v1/files` endpoint on older builds |
| HTTP machine memory read/write | TCP / HTTP | 80 | Yes | Host ops `mem_read_*` and `mem_write_screen_*`: `scripts/u64_http.py:287-318`; routes: `1541ultimate/software/api/route_machine.cc:70-203` | Strong coverage of the general memory API; firmware implements raw memory read here via `C64_DMA_RAW_READ`, not via a socket-64 opcode |
| HTTP machine debug register endpoint | TCP / HTTP | 80 | No | Route exists: `1541ultimate/software/api/route_machine.cc:205-213`; no matching host HTTP op in `scripts/u64_http.py:287-318` | Current script reads `0xD7FF` via raw memory API instead of the dedicated endpoint |
| HTTP machine lifecycle commands (`menu_button`, `reset`, `reboot`, `pause`, `resume`, `poweroff`, `measure`) | TCP / HTTP | 80 | No | Routes: `1541ultimate/software/api/route_machine.cc:22-68`, `1541ultimate/software/api/route_machine.cc:297`; no matching host ops in `scripts/u64_http.py:287-318` | Reachable but destructive or stateful |
| HTTP runners endpoints | TCP / HTTP | 80 | No | Routes: `1541ultimate/software/api/route_runners.cc:13-125`; no matching host ops in `scripts/u64_http.py` | Reachable but highly state-changing |
| HTTP streams endpoints | TCP / HTTP | 80 | No | Routes: `1541ultimate/software/api/route_streams.cc:17-65`; host stream control uses raw socket 64 instead: `scripts/u64_stream.py:148-149`, `scripts/u64_stream.py:436-446` | Different ingress path to the same stream subsystem |
| FTP control channel | TCP | 21 | Yes | Host connects/logs in: `scripts/u64_ftp.py:309-315`, `scripts/u64_ftp.py:747-839`; firmware listener/auth: `1541ultimate/software/network/ftpd.cc:191-238`, `1541ultimate/software/network/ftpd.cc:379-402` | Firmware uses the shared network password; the current host CLI models FTP password separately |
| FTP passive data channel | TCP | 51000-60999 | Yes | Host uses PASV and LIST/NLST/RETR/STOR: `scripts/u64_ftp.py:309-315`, `scripts/u64_ftp.py:719-745`; firmware PASV bind range: `1541ultimate/software/network/ftpd.cc:246`, `1541ultimate/software/network/ftpd.cc:622-627`, `1541ultimate/software/network/ftpd.cc:980-1010` | Covered well |
| Telnet remote menu | TCP | 23 | Partial | Host menu ops: `scripts/u64_telnet.py:794-824`, `scripts/u64_telnet.py:825-903`; firmware listener/auth: `1541ultimate/software/network/socket_gui.cc:33-104`, `1541ultimate/software/network/socket_gui.cc:179-217` | Functionally covered only when network password is empty |
| Raw socket command service | TCP | 64 | Partial | Firmware listener and commands: `1541ultimate/software/network/socket_dma.cc:40-56`, `1541ultimate/software/network/socket_dma.cc:90-106`, `1541ultimate/software/network/socket_dma.cc:364-475`; host only touches stream on/off commands through `u64_stream`: `scripts/u64_stream.py:148-149`, `scripts/u64_stream.py:436-446`; default script enables streams only in soak profile: `scripts/u64_connection_test.py:56-57`, `scripts/u64_connection_test.py:701-739` | This is the DMA-capable TCP endpoint; current coverage does not prove authenticate/identify and does not document the write-capable command family explicitly |
| UDP identify responder | UDP | 64 | No | Firmware UDP listener: `1541ultimate/software/network/socket_dma.cc:480-599`; no UDP identify host probe exists in `scripts/` | Unique externally reachable service missing entirely |
| ACIA modem listener | TCP | Default 3000, configurable | No | Firmware listener setup: `1541ultimate/software/io/acia/modem.cc:71`, `1541ultimate/software/io/acia/modem.cc:863-890`, `1541ultimate/software/io/acia/modem.cc:921-925`; generic listener impl: `1541ultimate/software/io/acia/listener_socket.cc:63-84` | Conditional surface; absent from current script |

## 6. Gap Analysis

### Critical gaps

#### 6.1 UDP identify responder on port 64 is completely untested

Why it is uncovered:

- The production firmware starts a UDP listener on port `64` in `SocketDMA::identThread` (`1541ultimate/software/network/socket_dma.cc:480-599`).
- No host probe module sends a UDP datagram to port `64` or validates the identify response.

Why it should be covered:

- It is a distinct externally reachable service.
- It is low risk to probe.
- It is a primary discovery path for device identification.

Classification: **critical**

#### 6.2 The raw socket command service on TCP port 64 is only incidentally covered

Why it is uncovered:

- The firmware exposes a dedicated TCP listener on port `64` (`1541ultimate/software/network/socket_dma.cc:364-475`).
- The current script only touches this service indirectly through stream enable/disable commands in `u64_stream.py` (`scripts/u64_stream.py:148-149`, `scripts/u64_stream.py:436-446`).
- There is no first-class probe that proves `AUTHENTICATE`, `IDENTIFY`, `DEBUG_REG`, or basic command framing.
- There is also no documentation in the current connection-test spec that this TCP endpoint is the DMA-capable transport exposing write/load/execute commands such as `SOCKET_CMD_DMA`, `SOCKET_CMD_DMARUN`, `SOCKET_CMD_DMAJUMP`, `SOCKET_CMD_DMAWRITE`, `SOCKET_CMD_REUWRITE`, and `SOCKET_CMD_KERNALWRITE` (`1541ultimate/software/network/socket_dma.cc:112-155`).
- Coverage disappears entirely when streams are disabled, including the `stress` profile.

Why it should be covered:

- Port `64` is the Ultimate-specific control ingress.
- It is the DMA-capable ingress for load/write/execute plus stream/debug/flash control.
- A correct test plan must distinguish between the safe subset that belongs in generic connectivity coverage and the destructive DMA-capable subset that should remain excluded.
- It is materially different from HTTP, FTP, and Telnet.

Classification: **critical**

#### 6.3 Shared password-protected HTTP, Telnet, FTP, and raw-socket deployments are not modeled consistently

Why it is uncovered:

- HTTP requires `X-Password` when `CFG_NETWORK_PASSWORD` is set (`1541ultimate/software/api/routes.h:304-313`).
- Telnet prompts for the network password (`1541ultimate/software/network/socket_gui.cc:33-104`).
- FTP `PASS` also checks the same `CFG_NETWORK_PASSWORD` (`1541ultimate/software/network/ftpd.cc:392-402`).
- Raw socket commands require `SOCKET_CMD_AUTHENTICATE` before any other command (`1541ultimate/software/network/socket_dma.cc:90-106`).
- The current script models FTP as having its own password option instead of treating the device network password as a single ingress-wide credential, and it still lacks general HTTP/Telnet/raw64 password plumbing (`scripts/u64_connection_test.py:357`, contrasted with the absence of HTTP/Telnet/raw64 password support in `scripts/u64_http.py`, `scripts/u64_telnet.py`, and `scripts/u64_stream.py`).

Why it should be covered:

- These services are production ingress surfaces gated by one shared device password.
- Without a single consistent password model the script can report false negatives on secured devices and can misrepresent how the firmware actually authenticates.

Classification: **critical**

### Useful gaps

#### 6.4 The ACIA modem listener is not covered

Why it is uncovered:

- The modem listener is production code and externally reachable when enabled (`1541ultimate/software/io/acia/modem.cc:863-890`, `1541ultimate/software/io/acia/modem.cc:921-925`).
- No host-side modem probe exists.

Why it should be covered:

- It is a real inbound TCP surface.
- It is feature-conditional rather than universally enabled.

Classification: **useful**

#### 6.5 The dedicated HTTP stream-control ingress is not covered

Why it is uncovered:

- `/v1/streams/*` exists as an HTTP control surface (`1541ultimate/software/api/route_streams.cc:17-65`).
- The current script controls streams only via raw socket port `64` (`scripts/u64_stream.py:148-149`, `scripts/u64_stream.py:436-446`).

Why it should be covered:

- It is a distinct ingress path.
- It is lower priority than port `64` raw control because it targets the same `DataStreamer` backend.

Classification: **useful**

#### 6.6 Debug stream control is not exercised by the host CLI even though the receiver already supports it

Why it is uncovered:

- Firmware exposes debug stream control via raw socket commands and HTTP stream routes (`1541ultimate/software/network/socket_dma.cc:45-47`, `1541ultimate/software/network/socket_dma.cc:233-266`; `1541ultimate/software/api/route_streams.cc:17-65`).
- `u64_stream.py` already implements `StreamKind.DEBUG`, packet parsing, and tracking (`scripts/u64_stream.py:44-53`, `scripts/u64_stream.py:118-160`, `scripts/u64_stream.py:402-459`).
- `u64_connection_test.py` intentionally restricts CLI stream choices to audio/video (`scripts/u64_connection_test.py:56-57`).

Why it should be covered:

- This is a cheap additive enhancement if debug stream coverage matters.

Classification: **useful**

### Out-of-scope gaps for the generic connection loop

These endpoints are reachable, but they should **not** be added to the default connectivity soak/stress loop because they are destructive, persistent, or workload-specific:

- HTTP machine lifecycle commands: reset, reboot, pause, resume, poweroff (`1541ultimate/software/api/route_machine.cc:22-68`)
- HTTP config persistence and reset operations: `load_from_flash`, `save_to_flash`, `reset_to_default` (`1541ultimate/software/api/route_configs.cc:282-339`)
- HTTP runner launch endpoints (`1541ultimate/software/api/route_runners.cc:13-125`)
- HTTP drive mutation endpoints such as mount, ROM load, set_mode (`1541ultimate/software/api/route_drives.cc:70-162`)
- HTTP file creation endpoints (`1541ultimate/software/api/route_files.cc:72-156`)

Classification: **out of scope** for the existing generic connectivity script.

## 7. Proposed Extensions (Minimal Invasive)

The following changes satisfy the coverage gaps without restructuring the existing script.

### 7.1 Add a first-class UDP identify probe

Proposed probe name: `ident`

- Protocol and method: UDP datagram to port `64`
- Behavior:
  - send `json<nonce>` to `<host>:64`
  - wait for one JSON reply
  - validate that the response contains at least `product`, `firmware_version`, `hostname`, and echoes the caller string in `your_string`
- Timeout strategy:
  - socket timeout `1.0s`
  - up to `2` retries before failure
- Success criteria:
  - response received from the target IP
  - valid JSON parse
  - required fields present and non-empty

Why this is minimal:

- It is a standalone additive probe.
- It uses only the Python standard library.
- It does not perturb device state.

### 7.2 Add a first-class raw socket probe for TCP port 64

Proposed probe name: `raw64`

- Protocol and method: TCP connection to port `64`, binary command framing
- This is the DMA-capable TCP endpoint, not the UDP identify endpoint.
- Firmware command surface visible in `socket_dma.cc` includes authentication, identify, DMA load/jump/write, keyboard injection, reset, poweroff, mount/run image, stream control, flash read, and debug-register access (`1541ultimate/software/network/socket_dma.cc:27-56`, `1541ultimate/software/network/socket_dma.cc:94-311`)
- The current firmware does **not** expose a general raw-memory-read opcode on socket 64 even though the subsystem supports `C64_DMA_RAW_READ`; generic memory reads are instead exposed through HTTP `/machine/readmem` (`1541ultimate/software/api/route_machine.cc:170-203`)
- Behavior:
  - connect to `<host>:64`
  - if a network password is configured, send `SOCKET_CMD_AUTHENTICATE (0xFF1F)` first
   - `smoke`: send `SOCKET_CMD_IDENTIFY (0xFF0E)` and require a non-empty title response
   - `read`: perform non-destructive reads such as `SOCKET_CMD_DEBUG_REG (0xFF76)` with `len=0`, and optionally `SOCKET_CMD_READFLASH (0xFF75)` metadata selectors for page size / page count only
   - `readwrite`: perform only reversible state changes, such as debug-register write-and-restore in one session, stream enable/disable with prompt cleanup when a receiver is active, or a tightly bounded `SOCKET_CMD_DMAWRITE` write-and-restore against an explicitly safe scratch location if restoration can be proved through a trusted readback path such as HTTP `/machine/readmem`
- Timeout strategy:
  - connect timeout `2s`
  - command response timeout `1s`
- Success criteria:
  - authentication returns success byte `1` when required
  - identify response length > 0
   - read-mode commands return the expected fixed-size or metadata responses
   - any readwrite-mode action verifies cleanup or restoration before success is reported

Explicitly out of scope for the generic connection test even though the endpoint exposes them:

- `SOCKET_CMD_DMA`, `SOCKET_CMD_DMARUN`, `SOCKET_CMD_DMAJUMP`
- `SOCKET_CMD_KEYB`
- `SOCKET_CMD_RESET`, `SOCKET_CMD_POWEROFF`
- `SOCKET_CMD_REUWRITE`, `SOCKET_CMD_KERNALWRITE`
- `SOCKET_CMD_MOUNT_IMG`, `SOCKET_CMD_RUN_IMG`, `SOCKET_CMD_RUN_CRT`
- `SOCKET_CMD_LOADSIDCRT`, `SOCKET_CMD_LOADBOOTCRT`
- full flash-page extraction via `SOCKET_CMD_READFLASH` page reads

`SOCKET_CMD_DMAWRITE` is the one DMA-style memory command that may be considered for opt-in `readwrite` coverage, but only under all of the following conditions:

- the address range is explicitly chosen as a safe scratch location
- the original bytes are read first through a trusted existing read path
- the original bytes are restored in the same probe session
- the probe reports success only after restoration is verified
- the action stays out of the default soak/stress loop

Do not infer additional read commands from `1541ultimate/python/sock.py`. That helper includes `0xFF73` / `0xFF74` examples, but those opcodes are not implemented in the current `socket_dma.cc` command switch and should not drive coverage requirements.

Why this is minimal:

- It reuses the existing host-side raw socket and command idioms already present in `u64_stream.py`.
- It does not require new dependencies.
- It deliberately constrains the endpoint to safe smoke/read/readwrite activities instead of the full destructive command set.

### 7.3 Add shared network-password plumbing

Proposed additive settings:

- env/CLI: `NETWORK_PASSWORD` / `--network-password`

Use it in the following places:

- HTTP: add `X-Password` header to every request when configured
- Telnet: detect `Password:` prompt and send the configured password before continuing the existing menu flow
- raw64: send `SOCKET_CMD_AUTHENTICATE` before any other command when configured
- FTP: send the same shared network password because firmware `PASS` auth checks `CFG_NETWORK_PASSWORD`

Why this is minimal:

- It does not change probe structure.
- It aligns the host-side probe model with the firmware's actual ingress authentication behavior without changing protocol semantics.

### 7.4 Add an optional modem probe

Proposed probe name: `modem`

- Protocol and method: TCP connect to configured modem listener port
- Behavior:
  - connect to `MODEM_PORT` (default `3000` only if explicitly requested)
  - read up to one banner/offline/busy message
  - classify the reply as `connected`, `busy`, or `offline`
  - disconnect without sending modem commands
- Timeout strategy:
  - connect timeout `2s`
  - receive timeout `1s`
- Success criteria:
  - successful TCP connect plus any readable banner text, or explicit busy/offline text

Why this is minimal:

- It is optional and should not be in default profiles.
- It does not require ACIA-state mutation.

### 7.5 Keep debug-stream CLI exposure optional and secondary

The firmware exposes debug-stream on/off commands on the raw socket endpoint (`1541ultimate/software/network/socket_dma.cc:45-50`, `1541ultimate/software/network/socket_dma.cc:233-266`), and the host receiver already knows how to parse debug stream packets (`scripts/u64_stream.py:44-53`, `scripts/u64_stream.py:118-160`, `scripts/u64_stream.py:402-459`).

However, a separate debug-stream CLI expansion is not required to satisfy the core gap. If stream control is used as the reversible `raw64` readwrite activity, it should stay behind explicit opt-in rather than becoming part of the default connectivity profiles.

## 8. Risks And Limitations

1. The generic connection script should remain non-destructive by default.

   This is why reset/reboot/poweroff, config persistence, runner launch, drive mutation endpoints, and the DMA-capable write/load/execute socket64 commands are intentionally excluded from the proposed default loop.

2. FTP data coverage is necessarily dynamic.

   The data port is allocated from the passive range starting at `51000` (`1541ultimate/software/network/ftpd.cc:246`, `1541ultimate/software/network/ftpd.cc:980-1010`), so the probe should validate behavior, not hardcode one data port.

3. ICMP coverage is stack-level rather than service-level.

   That is acceptable for ping coverage, but it does not say anything about any higher-layer daemon.

4. The modem surface is conditional.

   It is only reachable when the build has ACIA capability and the modem config enables a listening port (`1541ultimate/software/io/acia/modem.cc:921-925`, `1541ultimate/software/io/acia/modem.cc:863-890`).

5. Stream traffic itself is outbound.

   The inbound surface here is the control path (`raw64` or HTTP `/streams/*`), not the UDP payload transmission generated by `DataStreamer`.

## 9. Explicitly Not Covered

The following code was found during the repo scan but is intentionally excluded from the production inbound surface or from the default extension plan.

### 9.1 Outbound-only code paths

- Remote syslog client: opens a UDP socket and sends to a configured remote server, but does not listen inbound (`1541ultimate/software/network/syslog.cc:110-137`)
- SNTP client startup: outbound time synchronization (`1541ultimate/software/network/sntp_time.cc:18`)
- UDP stream transmission: outbound data packets sent by `DataStreamer` (`1541ultimate/software/io/network/data_streamer.cc:186`, `1541ultimate/software/io/network/data_streamer.cc:203`, `1541ultimate/software/io/network/data_streamer.cc:281-299`)
- Modem outgoing caller path: outbound TCP `connect()` initiated by dial commands (`1541ultimate/software/io/acia/modem.cc:521`)

### 9.2 Example, contrib, or test-only listeners not wired into production startup

- `1541ultimate/software/network/echo.c` binds a TCP echo server on port `7` (`1541ultimate/software/network/echo.c:79-86`), but no production init hook was found for `echo_init()`; the only direct calls are in lwIP example code (`1541ultimate/software/lwip/contrib/examples/example_app/test.c:560-566`)
- standalone HTTP demo trees under `1541ultimate/software/httpd/` are library/example code; the production runtime entry is `1541ultimate/software/network/httpd.cc`

### 9.3 Reachable but intentionally excluded from the generic connection loop

- state-resetting machine endpoints
- persistent configuration write/reset endpoints
- runner execution endpoints
- file/image creation endpoints
- drive mutation endpoints

These should be handled in dedicated stateful test suites, not in the generic connectivity script.

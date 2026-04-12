# Network Root Cause

Date: 2026-04-12
Status: Burst-collapse mechanism identified; broader intermittent timing proof still open

## Confidence

Above `90%` for the specific question of why an aggressive same-host vivipulse burst can take down REST, FTP, and TELNET on 1541ultimate targets.

## Burst-Collapse Mechanism

The linked 1541ultimate sources point to shared network resource exhaustion driven by task leaks and shallow listener limits, not a single REST/FTP/TELNET parser crash.

Confirmed source evidence:
- `1541ultimate/software/network/socket_gui.cc`
	- telnet listens with backlog `2`
	- every accepted telnet socket spawns a new `Socket Gui Task`
	- completed or failed telnet sessions end in `vTaskSuspend(NULL)` instead of `vTaskDelete(NULL)`
	- the listener returns on `accept()` error, so telnet can stop entirely after resource pressure
- `1541ultimate/software/network/ftpd.cc`
	- FTP control listens with backlog `2`
	- each control connection spawns a new `FTP Task`
	- passive-mode data sockets spawn a separate `FTP Data` task
	- that passive data accept task also ends in `vTaskSuspend(NULL)` instead of deleting itself
- `1541ultimate/software/network/config/lwipopts.h`
	- `MEMP_NUM_NETCONN = 16`
	- `MEMP_NUM_TCP_PCB = 30`
	- `TCP_LISTEN_BACKLOG = 0`, so lwIP backlog protection is disabled beneath those service listeners
- `1541ultimate/software/httpd/c-version/lib/server.h`
	- `MAX_HTTP_CLIENT = 4`
- `1541ultimate/software/httpd/c-version/lib/server.c`
	- HTTP stops accepting once that small client pool is full

## Interpretation

During an aggressive same-host burst:

1. telnet creates a fresh FreeRTOS task per probe and never deletes it
2. passive FTP probes create an extra FreeRTOS task per data accept and never delete it
3. the global lwIP/socket pool is small enough that brief concurrency spikes can exhaust shared connection state
4. once task creation or accepts start failing, the failure is not isolated to one protocol because HTTP, FTP, and TELNET all share the same TCP/socket budget

This explains the observed "all three protocols timed out together" failure mode without needing a separate parser bug in each daemon.

## How To Avoid Full Collapse

On the vivipi side:
- keep same-host concurrency disabled for 1541ultimate targets
- keep FTP probes control-channel only: login, `PWD`, `QUIT`
- do not run telnet in aggressive burst/search mode; if telnet coverage is required, run it at low frequency with generous spacing
- prefer a larger same-host backoff, at least `1000 ms`, when probing Ultimate devices repeatedly

On the 1541ultimate side:
- replace `vTaskSuspend(NULL)` with `vTaskDelete(NULL)` for completed telnet session tasks
- replace `vTaskSuspend(NULL)` with `vTaskDelete(NULL)` for completed passive FTP data accept tasks
- make the telnet listener survive transient `accept()` failures instead of returning permanently
- consider increasing `MEMP_NUM_NETCONN`, `MEMP_NUM_TCP_PCB`, and the effective listener backlog only after the task leaks are fixed

## Still Open

The narrower earlier question about the intermittent second-pass FTP connect stall remains open as a timing/reproduction problem. The source inspection above explains the full burst-induced network collapse, but not yet every isolated intermittent stall seen outside the aggressive burst profile.
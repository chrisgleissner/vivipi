# Ultimate 64 Incomplete Smoke Root Cause

Date: 2026-04-14

## Scope

This note summarizes the confirmed firmware-side findings from the incomplete smoke reproducer, the telnet stress reproducer, and the minimal fixes applied in the firmware.

## Reproducer

```bash
./scripts/u64_connection_test.py --profile soak --mode incomplete --surface smoke -d 0 --duration-s 7200
```

Full degradation (all network listeners and non-responsive menu button) observed on 14 April 2026 after 569 seconds:

```
99_ms=36 telnet_median_ms=46 telnet_p90_ms=52 telnet_p99_ms=62"
2026-04-14T08:29:02Z protocol=ping result=OK detail="runner=1 iteration=6560 ping_reply_ms=0.491 latency_ms=7"
2026-04-14T08:29:02Z protocol=http result=OK detail="runner=1 iteration=6560 surface=smoke op=get_version_smoke http_status=200 body_bytes=43 json_type=dict latency_ms=11"
2026-04-14T08:29:02Z protocol=ftp result=OK detail="runner=1 iteration=6560 surface=smoke op=ftp_greeting_only_quit ftp greeting ready latency_ms=16"
2026-04-14T08:29:02Z protocol=telnet result=OK detail="runner=1 iteration=6560 surface=smoke op=telnet_initial_read_classify banner ready latency_ms=57"
2026-04-14T08:29:02Z protocol=iteration result=INFO detail="runner=1 iteration=6560 runtime_s=566 host=u64 ping_median_ms=6 ping_p90_ms=8 ping_p99_ms=9 http_median_ms=12 http_p90_ms=23 http_p99_ms=30 ftp_median_ms=15 ftp_p90_ms=26 ftp_p99_ms=36 telnet_median_ms=46 telnet_p90_ms=52 telnet_p99_ms=62"
2026-04-14T08:29:03Z protocol=ping result=OK detail="runner=1 iteration=6570 ping_reply_ms=0.485 latency_ms=7"
2026-04-14T08:29:03Z protocol=http result=OK detail="runner=1 iteration=6570 surface=smoke op=get_version_smoke http_status=200 body_bytes=43 json_type=dict latency_ms=11"
2026-04-14T08:29:03Z protocol=ftp result=OK detail="runner=1 iteration=6570 surface=smoke op=ftp_greeting_only_quit ftp greeting ready latency_ms=15"
2026-04-14T08:29:03Z protocol=telnet result=OK detail="runner=1 iteration=6570 surface=smoke op=telnet_initial_read_classify banner ready latency_ms=49"
2026-04-14T08:29:03Z protocol=iteration result=INFO detail="runner=1 iteration=6570 runtime_s=567 host=u64 ping_median_ms=6 ping_p90_ms=8 ping_p99_ms=9 http_median_ms=12 http_p90_ms=23 http_p99_ms=30 ftp_median_ms=15 ftp_p90_ms=27 ftp_p99_ms=36 telnet_median_ms=46 telnet_p90_ms=52 telnet_p99_ms=62"
2026-04-14T08:29:03Z protocol=ping result=OK detail="runner=1 iteration=6580 ping_reply_ms=0.482 latency_ms=7"
2026-04-14T08:29:03Z protocol=http result=OK detail="runner=1 iteration=6580 surface=smoke op=get_version_smoke http_status=200 body_bytes=43 json_type=dict latency_ms=24"
2026-04-14T08:29:03Z protocol=ftp result=OK detail="runner=1 iteration=6580 surface=smoke op=ftp_greeting_only_quit ftp greeting ready latency_ms=14"
2026-04-14T08:29:03Z protocol=telnet result=OK detail="runner=1 iteration=6580 surface=smoke op=telnet_initial_read_classify banner ready latency_ms=42"
2026-04-14T08:29:04Z protocol=iteration result=INFO detail="runner=1 iteration=6580 runtime_s=568 host=u64 ping_median_ms=6 ping_p90_ms=8 ping_p99_ms=9 http_median_ms=12 http_p90_ms=23 http_p99_ms=30 ftp_median_ms=15 ftp_p90_ms=27 ftp_p99_ms=36 telnet_median_ms=46 telnet_p90_ms=52 telnet_p99_ms=62"
2026-04-14T08:29:04Z protocol=ping result=OK detail="runner=1 iteration=6590 ping_reply_ms=2.27 latency_ms=5"
2026-04-14T08:29:04Z protocol=http result=OK detail="runner=1 iteration=6590 surface=smoke op=get_version_smoke http_status=200 body_bytes=43 json_type=dict latency_ms=12"
2026-04-14T08:29:04Z protocol=ftp result=OK detail="runner=1 iteration=6590 surface=smoke op=ftp_greeting_only_quit ftp greeting ready latency_ms=14"
2026-04-14T08:29:04Z protocol=telnet result=OK detail="runner=1 iteration=6590 surface=smoke op=telnet_initial_read_classify banner ready latency_ms=45"
2026-04-14T08:29:04Z protocol=iteration result=INFO detail="runner=1 iteration=6590 runtime_s=569 host=u64 ping_median_ms=6 ping_p90_ms=8 ping_p99_ms=9 http_median_ms=12 http_p90_ms=23 http_p99_ms=30 ftp_median_ms=15 ftp_p90_ms=27 ftp_p99_ms=36 telnet_median_ms=46 telnet_p90_ms=52 telnet_p99_ms=62"
2026-04-14T08:29:07Z protocol=ping result=FAIL detail="runner=1 iteration=6597 PING u64 (192.168.1.13) 56(84) bytes of data. latency_ms=2007"
2026-04-14T08:29:19Z protocol=http result=FAIL detail="runner=1 iteration=6597 surface=smoke op=get_version_smoke timed out latency_ms=12364"
2026-04-14T08:29:32Z protocol=ftp result=FAIL detail="runner=1 iteration=6597 surface=smoke op=ftp_greeting_only_quit timed out latency_ms=12362"
2026-04-14T08:29:34Z protocol=telnet result=FAIL detail="runner=1 iteration=6597 surface=smoke op=telnet_initial_read_classify [Errno 113] No route to host latency_ms=2099"
2026-04-14T08:29:36Z protocol=ping result=FAIL detail="runner=1 iteration=6598 PING u64 (192.168.1.13) 56(84) bytes of data. latency_ms=2007"
2026-04-14T08:29:37Z protocol=http result=FAIL detail="runner=1 iteration=6598 surface=smoke op=get_version_smoke [Errno 113] No route to host latency_ms=1064"
```

Post test CLI checks demonstrate non-recovery of network services:

```
chris@mickey:~/dev/vivipi$ curl u64
curl: (7) Failed to connect to u64 port 80 after 3096 ms: Couldn't connect to server
chris@mickey:~/dev/vivipi$ ftp u64
ftp: Can't connect to `192.168.1.13:21': No route to host
ftp: Can't connect to `u64:ftp'
ftp> by
chris@mickey:~/dev/vivipi$ telnet u64
Trying 192.168.1.13...
telnet: Unable to connect to remote host: No route to host
chris@mickey:~/dev/vivipi$ ping u64
PING u64 (192.168.1.13) 56(84) bytes of data.
From mickey (192.168.1.185) icmp_seq=1 Destination Host Unreachable
From mickey (192.168.1.185) icmp_seq=2 Destination Host Unreachable
From mickey (192.168.1.185) icmp_seq=3 Destination Host Unreachable
```

Observed repository states:

- `vivipi`: `69f0125f`
- `1541ultimate`: `5d4a6173`

Observed result:

- The machine eventually enters an incorrect state with recurring short audio noise.
- The network services are still reachable when the audio symptom first becomes obvious.

Additional telnet reproducer:

```bash
./scripts/u64_connection_test.py --profile stress -d 0 --duration-s 30
```

Observed result:

- The telnet endpoint degrades within seconds under the stress profile.
- Stopping the host-side stress script does not restore telnet service.

## Confirmed Reset And Audio Facts

Relevant code:

- [software/io/command_interface/command_intf.cc](/home/chris/dev/vivipi/1541ultimate/software/io/command_interface/command_intf.cc#L96)
- [software/u64/u64_config.cc](/home/chris/dev/vivipi/1541ultimate/software/u64/u64_config.cc#L967)
- [software/u64/u64_config.cc](/home/chris/dev/vivipi/1541ultimate/software/u64/u64_config.cc#L982)
- [software/io/audio/sampler.h](/home/chris/dev/vivipi/1541ultimate/software/io/audio/sampler.h#L35)
- [software/u64/u64_config.cc](/home/chris/dev/vivipi/1541ultimate/software/u64/u64_config.cc#L1015)
- [software/u64/u64_config.cc](/home/chris/dev/vivipi/1541ultimate/software/u64/u64_config.cc#L1930)

Confirmed behavior:

- `ITU_INTERRUPT_RESET` is enabled as the C64 reset interrupt.
- The reset IRQ dispatches both `ResetInterruptHandlerCmdIf()` and `ResetInterruptHandlerU64()`.
- The command-interface reset task calls `Sampler::reset()` and disables all sampler voices.
- The U64 reset task reapplies board configuration on each reset event.
- That configuration path updates speaker, mixer, SID, and resampler state above the C64 application layer.

Engineering consequence:

- Repeated reset handling is sufficient to retrigger bundled-firmware audio state.
- The audible symptom does not by itself identify the exact source of the reset, and it does not by itself prove fallback ROM execution.

## Confirmed FTP And VFS Defects

### 1. Per-connection VFS lifetime leak

Relevant code:

- [software/network/ftpd.cc](/home/chris/dev/vivipi/1541ultimate/software/network/ftpd.cc#L278)
- [software/network/vfs.cc](/home/chris/dev/vivipi/1541ultimate/software/network/vfs.cc#L12)

Defect:

- `FTPDaemonThread::handle_connection()` opens a VFS root for each control connection.
- The thread lifetime did not release that VFS root.

### 2. Per-file wrapper leak

Relevant code:

- [software/network/vfs.cc](/home/chris/dev/vivipi/1541ultimate/software/network/vfs.cc#L48)

Defect:

- `vfs_close()` closed the underlying file but did not delete the `vfs_file_t` wrapper.

### 3. Per-directory wrapper leak

Relevant code:

- [software/network/vfs.cc](/home/chris/dev/vivipi/1541ultimate/software/network/vfs.cc#L130)

Defect:

- `vfs_closedir()` deleted the directory state but did not delete `dir->entry`.

### 4. Allocator mismatch on `vfs_getcwd()` buffers

Relevant code:

- [software/network/vfs.cc](/home/chris/dev/vivipi/1541ultimate/software/network/vfs.cc#L255)
- [software/network/ftpd.cc](/home/chris/dev/vivipi/1541ultimate/software/network/ftpd.cc#L358)
- [software/network/ftpd.cc](/home/chris/dev/vivipi/1541ultimate/software/network/ftpd.cc#L667)

Defect:

- `vfs_getcwd()` allocates with `malloc()`.
- FTP command handlers were freeing those buffers with `delete`.

### 5. Uninitialized socket-length argument

Relevant code:

- [software/network/ftpd.cc](/home/chris/dev/vivipi/1541ultimate/software/network/ftpd.cc#L269)

Defect:

- `getsockname()` was called with an uninitialized length value.

## Applied FTP And VFS Fixes

Applied changes:

- release the per-connection VFS root in `FTPDaemonThread` teardown
- release `renamefrom` in `FTPDaemonThread` teardown
- delete `vfs_file_t` wrappers in `vfs_close()`
- delete `vfs_dirent_t` wrappers in `vfs_closedir()`
- free `vfs_getcwd()` buffers with `free()` in FTP handlers
- initialize the `getsockname()` length argument before use
- make `STOR` return `425` when no data connection exists, matching the existing `LIST` and `RETR` handling

## Confirmed Telnet Defect

Relevant code:

- [1541ultimate/software/network/socket_gui.cc](1541ultimate/software/network/socket_gui.cc)
- [1541ultimate/software/network/socket_stream.cc](1541ultimate/software/network/socket_stream.cc)
- [1541ultimate/software/io/stream/host_stream.h](1541ultimate/software/io/stream/host_stream.h)
- [1541ultimate/software/userinterface/userinterface.cc](1541ultimate/software/userinterface/userinterface.cc)
- [1541ultimate/software/userinterface/editor.cc](1541ultimate/software/userinterface/editor.cc)

Defect:

- Each accepted telnet connection creates a dedicated remote UI task in `socket_gui_task()`.
- The stress profile intentionally opens telnet sessions, sends partial UI keystrokes such as `F2`, `F2 + Right`, or `F2 + Down + Enter`, and then drops the socket.
- Before the fix, `HostStream::exists()` and `HostStream::is_accessible()` always returned `true`, so the remote UI had no reliable host-liveness signal.
- `SocketStream` returned read and write errors to callers, but it did not close the underlying socket or expose that failure as durable stream state.
- Several blocking UI loops, including `run_remote()` and the modal helper loops in `UserInterface`, kept running until a widget returned a terminal code. When a disconnect happened inside a modal widget or editor path, the task could stay alive on a dead socket instead of unwinding the telnet session.

Engineering consequence:

- Short-lived aborted telnet sessions can accumulate unreaped remote UI tasks.
- Once enough leaked tasks exist, the telnet listener stops behaving like a recoverable transient failure and instead remains degraded after the host workload stops.

## Applied Telnet Fix

Applied changes:

- make `SocketStream` close itself on terminal read and write failures
- expose socket liveness through `Stream::is_alive()` and `HostStream::exists()`
- stop `run_remote()` when the telnet host disappears
- stop modal popup, choice, string-edit, and editor loops when the telnet host disappears
- make `Editor::poll()` treat socket disconnect as session exit

## Remaining Open Issue

The remaining unresolved part is not the presence of a reset-to-audio path; that is already confirmed. The unresolved part is what starts the repeated reset handling under the long-running incomplete smoke reproducer.

The next firmware investigation step should instrument reset frequency and reset origin rather than infer them from the audible symptom.

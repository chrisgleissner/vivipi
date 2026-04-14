# Ultimate 64 Firmware Fixed Defects

Date: 2026-04-14

## Purpose

This note is an audit of the currently outstanding, uncommitted changes in the `1541ultimate` repository. It lists each retained code change, the concrete defect or failure mode it addresses, and why that change stays.

This note does not restate the broader reset/audio investigation. For unresolved system-level behavior, see `docs/u64-firmware-smoke-root-cause.md`.

Audit conclusion:

- Every currently modified firmware file maps to either a confirmed defect or the minimum contract change required to make the telnet disconnect fix reliable.
- No outstanding code change was identified as superfluous during this review.

## Observed Stability Failures

The retained fixes address two observed stability classes:

- FTP/VFS control-plane degradation under repeated incomplete-smoke traffic.
- Telnet service degradation under short-lived interactive stress sessions that disconnect mid-UI flow.

## Change Audit

### 1. FTP control-session VFS root lifetime leak

Area:

- `software/network/ftpd.cc`
- `software/network/ftpd.h`

Observed error:

- `FTPDaemonThread::handle_connection()` opened a new VFS root for each FTP control connection.
- The thread teardown path did not release that VFS root.
- `renamefrom` also survived thread teardown if an RNFR/RNTO sequence was interrupted.

Retained code changes:

- Added an explicit `FTPDaemonThread` destructor.
- Released `renamefrom` with `delete[]`.
- Closed the per-thread VFS root with `vfs_closefs(vfs)`.

Justification:

- Repeated FTP connect/disconnect traffic leaked per-session state until the daemon task exited.
- This is directly tied to the long-running incomplete-smoke workload and must stay.

### 2. FTP file-wrapper lifetime leak

Area:

- `software/network/vfs.cc`

Observed error:

- `vfs_close()` closed the underlying `File` object but did not delete the `vfs_file_t` wrapper allocated for the FTP layer.

Retained code change:

- Deleted the `vfs_file_t` wrapper in `vfs_close()` after closing the file and clearing `open_file`.

Justification:

- Every completed FTP file operation leaked one wrapper object.
- This is a confirmed ownership bug in the FTP path and must stay.

### 3. FTP directory-wrapper lifetime leak

Area:

- `software/network/vfs.cc`

Observed error:

- `vfs_closedir()` released the directory entry list and the directory wrapper, but not `dir->entry`.

Retained code change:

- Deleted `dir->entry` before deleting the directory wrapper.

Justification:

- Every FTP directory enumeration leaked one `vfs_dirent_t` wrapper.
- This is a confirmed ownership bug in the FTP listing path and must stay.

### 4. Allocator mismatch on `vfs_getcwd()` results

Area:

- `software/network/vfs.cc`
- `software/network/ftpd.cc`

Observed error:

- `vfs_getcwd()` returns buffers allocated via `malloc()`.
- FTP handlers freed those buffers with `delete` instead of `free()`.

Retained code changes:

- Switched `cmd_pwd()` to `free()` the returned buffer.
- Switched `cmd_mkd()` to `free()` the returned buffer.

Justification:

- Mixed allocation/free APIs can corrupt the heap and destabilize long-running control-plane traffic.
- This is a concrete heap-integrity defect, not a stylistic cleanup.

### 5. Uninitialized `getsockname()` length argument

Area:

- `software/network/ftpd.cc`

Observed error:

- `getsockname()` was called with an uninitialized `socklen_t` argument.

Retained code change:

- Initialized the length variable to `sizeof(my_addr)` before the call.

Justification:

- The old call relied on undefined stack state when querying the bound address.
- This is not the primary stability root cause, but it is a correctness defect in the same hot path and should remain fixed.

### 6. `STOR` accepted a file open without a data connection

Area:

- `software/network/ftpd.cc`

Observed error:

- `cmd_stor()` opened the target file before checking whether a passive/active data connection had been established.
- If `connection` was null, the handler could not complete the transfer and needed to unwind manually.

Retained code change:

- Added an explicit `!connection` guard.
- Returned `425` and closed the opened VFS file wrapper on that path.

Justification:

- The handler now matches the existing `LIST`/`RETR` contract and does not leave partially initialized `STOR` state behind.
- This improves FTP error-path resiliency and is consistent with adjacent command handlers, so it stays.

### 7. Telnet remote-session tasks could survive dead sockets

Area:

- `software/network/socket_stream.cc`
- `software/network/socket_stream.h`
- `software/io/stream/stream.h`
- `software/io/stream/host_stream.h`
- `software/userinterface/userinterface.cc`
- `software/userinterface/editor.cc`

Observed error:

- Each telnet connection creates a dedicated remote UI task.
- Before the fix, read/write failures did not convert into durable stream liveness state.
- `HostStream::exists()` and `HostStream::is_accessible()` always returned `true`.
- Blocking UI loops such as `run_remote()`, `popup()`, `string_box()`, `choice()`, and `run_editor()` could therefore continue after the client had already disconnected.
- Under stress traffic that sends partial menu keystrokes and aborts the socket, these unreaped tasks accumulated and degraded the telnet service.

Retained code changes:

- Added `Stream::is_alive()` in `software/io/stream/stream.h`.
- Implemented `SocketStream::is_alive()` in `software/network/socket_stream.h`.
- Made `SocketStream::get_char()` and `SocketStream::transmit()` close the socket on terminal failures in `software/network/socket_stream.cc`.
- Made `HostStream::exists()` and `HostStream::is_accessible()` depend on stream liveness in `software/io/stream/host_stream.h`.
- Changed `UserInterface::run_remote()` to stop when the host disappears in `software/userinterface/userinterface.cc`.
- Changed the blocking modal helpers in `software/userinterface/userinterface.cc` to stop when the host disappears.
- Made `choice()` return `MENU_CLOSE` on disconnect so a dropped session cannot be misread as selecting index `0`.
- Made `Editor::poll()` convert socket disconnect to `MENU_EXIT` in `software/userinterface/editor.cc`.

Justification:

- Aborted telnet sessions now tear down deterministically instead of leaving orphaned UI tasks behind.
- `Stream::is_alive()` is required because `HostStream` otherwise has no transport-agnostic way to observe socket death.
- The `SocketStream` close-on-failure changes are required so liveness transitions from true to false exactly once and remain observable.
- The `HostStream` changes are required so the UI can consume that liveness state without socket-specific code.
- The `UserInterface` loop changes are required because several modal helpers are blocking loops outside the normal `pollFocussed()` unwind path.
- The `Editor::poll()` change is retained as a resiliency fix: after the loop guards were added it is no longer the sole escape path, but it preserves correct disconnect semantics for editor call sites that do not wrap polling in `host->exists()` checks.

## Summary

The outstanding 1541ultimate diff was reviewed hunk by hunk. The result is:

- retain all FTP/VFS fixes because they correct confirmed ownership, allocator, or error-path defects in the exercised control-plane code
- retain all telnet/UI fixes because they are the minimum complete liveness chain from socket failure to remote-task teardown

No currently outstanding firmware change was reverted during this review.

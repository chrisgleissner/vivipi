# U64 Soak Incomplete-Mode Investigation

## Scope

- Command under investigation: `./scripts/u64_connection_test.py --profile soak --mode incomplete --surface readwrite -d 0 --duration-s 7200`
- Primary log: `scripts/logs/u64_connection_test/30_soak_incomplete_firmware_patch.log`
- Driver code reviewed:
  - `scripts/u64_connection_test.py`
  - `scripts/u64_connection_runtime.py`
  - `scripts/u64_ftp.py`
  - `scripts/u64_telnet.py`
- Symlinked firmware tree reviewed:
  - `1541ultimate -> ../c64/1541ultimate`
  - `1541ultimate/software/network/ftpd.h`
  - `1541ultimate/software/network/ftpd.cc`
  - `1541ultimate/software/network/vfs.cc`
  - `1541ultimate/software/filemanager/filemanager.cc`
  - `1541ultimate/software/filesystem/filesystem_fat.cc`

## Executive Summary

- The soak log contains `166` `FAIL` records, all on `surface=readwrite`.
- The failure mix is dominated by FTP self-file operations, not by generic connectivity loss.
- Successful FTP operations continue after the first failure, including later uploads, renames, deletes, and `NLST /Temp` calls. That is inconsistent with a hard FTP daemon failure.
- The strongest driver-side finding is semantic drift in `incomplete` mode:
  - for non-smoke FTP, `--mode incomplete` currently executes the normal read/readwrite surface operations and merely avoids `QUIT`
  - for non-smoke Telnet, `--mode incomplete` currently executes the normal read/readwrite session operations and merely drops the socket at the end
- That behavior does not match the intended meaning of incomplete mode as clarified for this investigation: perform surface-appropriate commands, but terminate them mid-flow so server-side cleanup paths are exercised.
- The symlinked `1541ultimate` firmware remains relevant, but the current checked-out tree does not support the hypothesis that this soak failure is primarily the old `1 KiB` FTP data-buffer problem:
  - current `ftpd.h` already defines `FTPD_DATA_BUFFER_SIZE 8192`
  - current `sendfile()` / `receivefile()` already map setup, abort, and storage failures to `425` / `426` / `452`
- One firmware-side factor can still amplify the problem once the driver pollutes `/Temp`: `NLST` / `LIST` materialize and sort the full directory, and the log shows `/Temp` entry count growing from `4` to `122` before the first failure.
- The delayed onset is still not fully explained. The evidence supports a driver-originating workload problem plus possible firmware-side amplification, but not yet a complete root-cause proof for why failures begin only after roughly `21` minutes.

## Failure Summary

Total failures: `166`

| Count | Share | First occurrence | Last occurrence | Surface | Protocol | Operation | Signature |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 52 | 31.33% | 2026-04-17T10:49:39Z | 2026-04-17T11:11:36Z | readwrite | ftp | `ftp_rename_self_file` | `450 Requested file action not taken.` |
| 47 | 28.31% | 2026-04-17T10:49:56Z | 2026-04-17T11:11:28Z | readwrite | ftp | `ftp_upload_tiny_self_file` | `550 Requested action not taken.` |
| 43 | 25.90% | 2026-04-17T10:49:59Z | 2026-04-17T11:11:39Z | readwrite | ftp | `ftp_upload_large_self_file` | `550 Requested action not taken.` |
| 18 | 10.84% | 2026-04-17T10:51:21Z | 2026-04-17T11:09:00Z | readwrite | ftp | `ftp_download_large_self_file` | `550 Requested action not taken.` |
| 4 | 2.41% | 2026-04-17T10:56:27Z | 2026-04-17T11:06:10Z | readwrite | telnet | `set_vol_ultisid_1_plus_1_db` | `missing telnet text: Save changes to Flash` |
| 2 | 1.20% | 2026-04-17T10:52:19Z | 2026-04-17T10:56:54Z | readwrite | telnet | `set_vol_ultisid_1_0_db` | `missing telnet text: Save changes to Flash` |

## Observed Timing and Onset

- The first failure appears at `2026-04-17T10:49:39Z` on `ftp_rename_self_file`.
- The immediately preceding iteration summary at `2026-04-17T10:49:23Z` reports `runtime_s=1266`.
- The following iteration summary at `2026-04-17T10:49:40Z` reports `runtime_s=1283`.
- Based on the log, the failure onset is therefore around `21m 20s`, not at process start.
- The long delay before first failure remains unresolved and should be treated as an open question, not as explained.

## Successful Operations Still Occurring After Onset

The failing run does not degrade into total FTP outage.

- `ftp_delete_self_file` succeeds repeatedly after the first failure.
- `ftp_upload_tiny_self_file` still succeeds after onset.
- `ftp_upload_large_self_file` still succeeds after onset, including at `2026-04-17T11:11:24Z`.
- `ftp_rename_self_file` still succeeds after onset.
- `ftp_nlst_temp` still succeeds after onset.

Successful readwrite FTP operation counts across the log:

| Operation | Success count |
| --- | --- |
| `ftp_delete_self_file` | 22 |
| `ftp_download_large_self_file` | 14 |
| `ftp_download_tiny_self_file` | 19 |
| `ftp_list_root` | 21 |
| `ftp_nlst_root` | 23 |
| `ftp_nlst_temp` | 16 |
| `ftp_pwd` | 14 |
| `ftp_rename_self_file` | 13 |
| `ftp_upload_large_self_file` | 16 |
| `ftp_upload_tiny_self_file` | 21 |

This mixed success/failure pattern is a poor fit for a permanently broken firmware FTP service. It is a much better fit for a driver that is generating problematic state while still occasionally landing operations successfully.

## Driver Findings

### 1. Non-smoke FTP incomplete mode is currently running complete surface operations

Relevant code:

- `scripts/u64_ftp.py:707-732` defines the normal surface operation set, including:
  - `ftp_upload_tiny_self_file`
  - `ftp_download_tiny_self_file`
  - `ftp_upload_large_self_file`
  - `ftp_download_large_self_file`
  - `ftp_rename_self_file`
  - `ftp_delete_self_file`
- `scripts/u64_ftp.py:757-773` routes `correctness == INCOMPLETE` and `surface != SMOKE` to `surface_operations(...)`, not to `incomplete_operations(...)`.
- `scripts/u64_ftp.py:735-744` then executes those complete operations and merely closes the FTP socket without `QUIT`.

Implication:

- In non-smoke incomplete mode, the driver is not performing intentionally incomplete `STOR` / `RETR` / `LIST` style operations.
- Instead, it performs normal uploads, downloads, renames, and deletes against `/Temp`.
- That behavior matches the observed FTP failure mix exactly.

This is the strongest evidence that the current driver does not implement the intended semantics of incomplete mode.

### 2. Non-smoke Telnet incomplete mode is also running complete session operations

Relevant code:

- `scripts/u64_telnet.py:782-810` defines the normal read/readwrite surface operations, including real mixer writes.
- `scripts/u64_telnet.py:821-832` routes `correctness == INCOMPLETE` and `surface != SMOKE` to `surface_operations(...)`, not to `incomplete_operations(...)`.

Implication:

- The Telnet side of incomplete mode is also not restricted to abort-style partial interactions.
- The observed failures on `set_vol_ultisid_1_0_db` and `set_vol_ultisid_1_plus_1_db` are consistent with the driver performing real menu writes and then losing synchronization.

### 3. The driver steadily grows `/Temp` before failures begin

Observed `ftp_nlst_temp entries=` values:

| Timestamp | `/Temp` entries |
| --- | --- |
| 2026-04-17T10:28:27Z | 4 |
| 2026-04-17T10:29:06Z | 9 |
| 2026-04-17T10:34:04Z | 38 |
| 2026-04-17T10:37:24Z | 58 |
| 2026-04-17T10:39:29Z | 70 |
| 2026-04-17T10:42:54Z | 89 |
| 2026-04-17T10:45:41Z | 104 |
| 2026-04-17T10:47:44Z | 115 |
| 2026-04-17T10:48:59Z | 122 |
| 2026-04-17T10:57:36Z | 126 |
| 2026-04-17T11:00:32Z | 125 |
| 2026-04-17T11:00:51Z | 126 |

Implication:

- The driver is not maintaining a stable self-test footprint.
- File churn is cumulative enough to materially change the `/Temp` working set before the first failures.
- That makes the soak less about connection cleanup and more about stress from persistent artifact growth.

### 4. Failure latencies are driver-amplified

Observed failure timings cluster around:

- FTP upload/download failures: about `2.3s` to `2.4s`
- FTP rename failures: about `8.7s` to `9.3s`
- Telnet write failures: about `8.9s`

Relevant code:

- `scripts/u64_connection_runtime.py` retries retryable surface errors with delays of `0.10`, `0.25`, `0.50`, `1.00` seconds.
- `scripts/u64_ftp.py` adds additional inner retries for `450` / `550` verification cases.

Implication:

- These latencies should not be read as direct device execution times.
- They are consistent with nested client-side retry loops around the same underlying failure condition.

### 5. The Telnet failures line up with the same stressed iterations as slow FTP failures

Observed pairing from the log:

- `2026-04-17T10:52:19Z`: `ftp_rename_self_file` fails with `450` at `8766ms`, and `set_vol_ultisid_1_0_db` fails in the same iteration at `8949ms`
- `2026-04-17T10:56:27Z`: `ftp_rename_self_file` fails with `450` at `8783ms`, and `set_vol_ultisid_1_plus_1_db` fails in the same iteration at `8947ms`
- `2026-04-17T10:56:54Z`: `ftp_rename_self_file` fails with `450` at `8804ms`, and `set_vol_ultisid_1_0_db` fails in the same iteration at `8995ms`
- `2026-04-17T11:03:56Z`: `ftp_rename_self_file` fails with `450` at `8726ms`, and `set_vol_ultisid_1_plus_1_db` fails in the same iteration at `8933ms`
- `2026-04-17T11:05:38Z`: `ftp_rename_self_file` fails with `450` at `8786ms`, and `set_vol_ultisid_1_plus_1_db` fails in the same iteration at `9069ms`
- `2026-04-17T11:06:10Z`: `ftp_rename_self_file` fails with `450` at `8774ms`, and `set_vol_ultisid_1_plus_1_db` fails in the same iteration at `8994ms`

Implication:

- The Telnet failures do not look like an independent outage class.
- They line up with the same slow, stressed iterations that already contain FTP rename failures.
- That makes the Telnet errors better explained as spillover from the same incomplete-mode regression plus tighter Telnet timing assumptions, rather than as a separate root cause.

## Regression Scan Against The Last Known-Good Comparison Point

Comparison point used for this investigation:

- `a567636302c98accc2f9b6facb90c686ae9597e2` (`2026-04-16 14:28:59 +0100`, `Address PR review feedback`)

Why this point:

- It is the nearest commit before the probe behavior changes that started later on `2026-04-16`.
- At that point, incomplete FTP and incomplete Telnet still routed through their dedicated abort-style operation sets for all surfaces.

### Ranked Driver-Side Regression Candidates

#### 1. `f0ef5db` changed incomplete semantics for both FTP and Telnet

Commit:

- `f0ef5dba1d28012c22090b13840ddf9660bf92fc` (`2026-04-16 17:09:38 +0100`)

Change:

- FTP before `f0ef5db`: `run_probe()` routed `correctness == INCOMPLETE` to `incomplete_operations(...)` for all surfaces.
- FTP after `f0ef5db`: non-smoke incomplete mode routes to `surface_operations(...)` and then closes the socket without `QUIT`.
- Telnet before `f0ef5db`: `run_probe()` routed `correctness == INCOMPLETE` to `incomplete_operations(...)` for all surfaces.
- Telnet after `f0ef5db`: non-smoke incomplete mode routes to `surface_operations(...)` and drops the session afterward.

Why this is the strongest regression candidate:

- It directly explains why the failures now appear under full readwrite operation names such as `ftp_upload_large_self_file`, `ftp_rename_self_file`, and `set_vol_ultisid_1_plus_1_db`.
- It matches the user’s clarification that incomplete mode is supposed to perform surface-appropriate operations incompletely, not perform normal complete operations and only skip the final clean shutdown.
- It affects both FTP and Telnet, which matches the observed log pattern.

#### 2. `0c70aef` made FTP readwrite much heavier and more stateful

Commit:

- `0c70aef1e940022e13b8e18115f88541a59340b3` (`2026-04-16 17:01:23 +0100`)

Change:

- FTP readwrite before: `ftp_create_self_file`, `ftp_read_self_file`, `ftp_rename_self_file`, `ftp_delete_self_file`.
- FTP readwrite after: `ftp_upload_tiny_self_file`, `ftp_download_tiny_self_file`, `ftp_upload_large_self_file`, `ftp_download_large_self_file`, `ftp_rename_self_file`, `ftp_delete_self_file`.
- Added explicit tiny and large file sizes, including `256 KiB` uploads/downloads.

Why this likely contributed:

- The failure mix maps directly onto the newly added operations: tiny upload, large upload, large download, and rename.
- This increases filesystem churn, directory growth, and transfer pressure compared with the earlier create/read workload.
- On its own, this change may not have been enough to cause the regression, but combined with `f0ef5db` it turned incomplete mode into a much more mutating and expensive workload.

#### 3. `069023b` added FTP shared-state verification and more retry-sensitive failure detection

Commit:

- `069023ba4c52a405edd1b41f487ca390b3083d8f` (`2026-04-17 07:43:32 +0100`)

Change:

- Added shared confirmed and tentative self-file tracking.
- Added verification retries on `450` and `550` style FTP responses.
- Made upload, download, and rename depend on observed file-state verification.

Why this likely amplified the perceived failure:

- It can convert transient self-file inconsistencies into explicit probe failures instead of quietly continuing.
- It increases the amount of client-side time spent retrying and verifying around the same failing condition.
- It matches the observed `2.3s` and `8.8s` latency bands better than a direct device-only timing explanation.

Assessment:

- This looks more like a failure amplifier and reporter hardening change than the original semantic regression.
- It is still a plausible reason the same underlying problem became visible earlier or more consistently.

#### 4. `21aacb1`, `dcc1a103`, `06749ad`, and `ce9f287` made Telnet prompt detection tighter and more brittle under stress

Commits:

- `21aacb13104a433cc7f7aa2c8993fbb7cc128d39` (`2026-04-16 22:20:11 +0100`)
- `dcc1a103cc199bc612bb73dd14da727bffb6fdb5` (`2026-04-16 22:32:55 +0100`)
- `06749ad4b40c10e883bc5f5f6fcb18329789ff9d` (`2026-04-16 22:39:01 +0100`)
- `ce9f28705cc5d66c6dc362864a75a69a16844826` (`2026-04-16 22:43:14 +0100`)

Change:

- Reduced Telnet idle/read timing from `0.20s` with `3` empty reads to `0.12s` with `1` empty read, plus a `0.02s` quiet timeout after data.
- Added select-based readiness checks and more stateful menu/view tracking.
- Made `missing telnet text` and `verification mismatch` retryable surface errors.
- Tightened the mixer write flow around detection of the `Save changes to Flash` prompt.

Why this likely contributed:

- The observed Telnet failures are exactly `missing telnet text: Save changes to Flash`.
- Those failures occur only during the same slow iterations as FTP rename failures, which is when shorter read windows are most likely to miss a delayed prompt.
- This does not look like the primary regression, but it is a credible reason the Telnet side now reports a small number of explicit failures instead of silently surviving the same stressed windows.

#### 5. `u64_connection_test.py` now randomizes protocol and operation order

Change:

- Execution state now derives a random seed and shuffles per-iteration probe order.
- Operation selection is now permuted by protocol, surface, and cycle instead of walking a fixed round-robin order.

Why this likely contributed:

- It can move the first visible failure earlier by scheduling expensive FTP and Telnet operations in a different relative order.
- It can also make the Telnet write operations coincide more often with the same iterations that already contain slow FTP rename retries.
- This changes failure timing and observability even when the underlying bug is unchanged.

Assessment:

- This is an onset shaper, not the root semantic regression.

### Regression Summary

Most likely causal chain:

1. `f0ef5db` changed incomplete mode from abort-style probes into full readwrite probes for both FTP and Telnet.
2. `0c70aef` made the FTP readwrite workload materially heavier and more mutating.
3. `069023b` increased FTP verification sensitivity and retry time around the same failures.
4. Late-April-16 Telnet timing changes made a few stressed write iterations fail explicitly with `missing telnet text: Save changes to Flash`.
5. Randomized scheduling changed when the problem first becomes visible.

This combination explains both parts of the observed behavior:

- why most failures are now FTP readwrite mutations
- why a few Telnet write failures appear in the same stressed iterations

## Symlinked Firmware Findings

### 1. The current symlinked firmware already uses an `8 KiB` FTP data buffer

Relevant code:

- `1541ultimate/software/network/ftpd.h` defines `FTPD_DATA_BUFFER_SIZE 8192`.

Implication:

- The current checkout does not match the older `1 KiB` FTP buffer state described in earlier throughput notes.
- That older explanation should not be treated as the default explanation for this current soak issue.

### 2. The current symlinked firmware already propagates transfer failures for `STOR` and `RETR`

Relevant code:

- `1541ultimate/software/network/ftpd.cc:135-159` maps transfer outcomes as follows:
  - `FTP_TRANSFER_SETUP_FAILED -> 425`
  - `FTP_TRANSFER_ABORTED -> 426`
  - `FTP_TRANSFER_STORAGE_ERROR -> 452`
- `cmd_retr()` and `cmd_stor()` use `transfer_result_message(result)`.

Implication:

- The current `RETR` / `STOR` path is better behaved than the older unconditional-`226` implementation described elsewhere.
- That reduces confidence that the failing soak is primarily caused by stale firmware-side transfer result handling.

### 3. Directory enumeration still scales with directory size

Relevant code:

- `1541ultimate/software/network/vfs.cc:105-138` builds an `IndexedList<FileInfo *>` for directory contents.
- `1541ultimate/software/filemanager/filemanager.cc:108-140` enumerates all entries and then sorts them when `needs_sorting()` is true.
- `1541ultimate/software/filesystem/filesystem_fat.h` reports `needs_sorting() == true`.

Implication:

- A steadily growing `/Temp` directory can increase the cost of `NLST` / `LIST` over time.
- That is a plausible amplifier for the delayed onset.
- It does not by itself explain why the observed failures specifically center on rename and self-file upload/download operations.

### 4. Rename/open failures in firmware map naturally to the observed FTP reply codes

Relevant code:

- `RNTO` returns `450` when `vfs_rename()` fails.
- `STOR` returns `550` when `vfs_open(..., "wb")` fails.
- `RETR` returns `550` when `vfs_stat()` or `vfs_open(..., "rb")` fails.

Implication:

- The observed `450` and `550` failures are compatible with filesystem-level refusal or path-state problems.
- Nothing in the reviewed firmware code points to a special long-running FTP leak mechanism that would be more likely than the driver-side workload problem.

## Most Likely Interpretation

### Primary diagnosis

The current soak failure is more likely caused by the test driver than by the current symlinked FTP firmware.

Reasoning:

- The failure signatures line up with the driver executing complete self-file mutations in incomplete mode.
- The intended incomplete semantics are not what the code currently does for non-smoke FTP and Telnet.
- `/Temp` entry count grows steadily before onset.
- Successes continue after onset, including successful later uploads.
- Independent manual FTP behavior after the soak failure is therefore not surprising.

### Secondary firmware-side amplifier

The symlinked firmware can plausibly amplify the driver problem because larger `/Temp` directories cost more to enumerate and sort.

Reasoning:

- The driver clearly increases `/Temp` occupancy.
- The firmware clearly does full directory materialization and sorting for FAT-backed listing.
- That can increase pressure and latency as the soak progresses.

### Remaining uncertainty

The long delay until failures begin is not fully explained.

Open possibilities include:

- accumulation of self-test files in `/Temp`
- server-side cleanup lag after repeated client aborts and socket drops
- FAT directory/state behavior that only becomes visible after enough churn
- interaction between ongoing streaming load and repeated FTP/Telnet retries

At this point, the delay should be treated as an investigation gap, not as resolved.

## Minimal-Invasive Fix Plan

### Plan A: Fix incomplete-mode semantics in the driver first

1. Change non-smoke FTP incomplete mode to select abort-style incomplete operations instead of normal surface operations.
2. Change non-smoke Telnet incomplete mode to select abort-style incomplete operations instead of normal surface operations.
3. Keep the surface contract, but make each operation incomplete by design.

Expected effect:

- The soak begins testing server-side cleanup semantics again instead of testing long-running self-file churn.

### Plan B: Mirror the surface with incomplete variants, not with complete operations

For FTP:

- `smoke`: greeting/login/PASV-level aborts are fine.
- `read`: use partial `LIST`, partial `NLST`, partial `RETR`.
- `readwrite`: use partial `STOR` and partial `RETR`; optionally add partial directory ops.
- Avoid `rename` and `delete` as core incomplete-mode operations because they do not model mid-transfer cleanup.

For Telnet:

- `read`: open menu, begin read/navigation, then abort.
- `readwrite`: enter write flow, start edit/confirmation path, then abort before normal completion.
- Do not perform fully completed value writes in incomplete mode.

### Plan C: Bound the client-created artifact footprint

1. Move incomplete-mode FTP artifacts to a dedicated prefix or path.
2. Add deterministic pre-run cleanup for that prefix/path.
3. Periodically log tracked-file counts and `/Temp` counts.

Expected effect:

- Keeps the soak focused on connection cleanup rather than gradual filesystem pollution.

### Plan D: Add targeted investigation for the delayed onset

1. Log selected incomplete operation names on every iteration around the failure window.
2. Log `/Temp` entry count and tracked self-file counts every `N` iterations.
3. Log whether the failing path already exists when `550` occurs on `STOR`.
4. If needed, add a temporary firmware-side counter for aborted passive data connections and open/closed file handles.

Expected effect:

- Distinguishes client artifact accumulation from delayed firmware cleanup failure.

## Recommended Order

1. Fix the driver’s incomplete-mode dispatch for FTP and Telnet.
2. Re-run the same soak profile unchanged otherwise.
3. Compare:
   - failure count
   - `/Temp` entry growth
   - first-failure time
   - whether manual FileZilla transfers remain unaffected
4. Only if failures persist after the semantic fix, instrument the symlinked firmware cleanup paths.

## Concrete Next Change Set

Minimal code changes with the best signal-to-risk ratio:

1. In `scripts/u64_ftp.py`, route `correctness == INCOMPLETE` to incomplete operation sets for all surfaces.
2. In `scripts/u64_telnet.py`, do the same.
3. Expand the FTP incomplete read/readwrite operation set so it includes partial `RETR` in addition to existing partial `LIST` / `NLST` / `STOR` coverage.
4. Add explicit cleanup and counters for incomplete-mode temporary artifacts.
5. Add a focused regression test for operation selection so incomplete mode cannot silently fall back to complete operations again.

## Bottom Line

- The log evidence currently points more strongly at the test driver than at the current symlinked firmware.
- The most important bug is not yet a proven server leak; it is that `incomplete` mode currently behaves like a mostly complete readwrite workload for non-smoke FTP and Telnet.
- The delayed onset remains real and unexplained, and should be investigated further even after the driver fix.
- The symlinked firmware is still worth monitoring, but the first corrective action should be in the driver.
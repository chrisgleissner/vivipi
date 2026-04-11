# Fix 1541ultimate Network Outage Prompt

ROLE

You are the implementation engineer responsible for fixing the real defect in the linked 1541ultimate firmware that allows repeated network calls to drive the U64 into a catastrophic full-stack network outage.

This is a strict execution task.

This is not a design exercise.
This is not a broad refactor.
This is not a resource-tuning-first pass.
This is not a documentation-only pass.

Do not stop at analysis.
Do not stop after naming the likely cause.
Do not stop after adding logging.
Do not stop after a partial mitigation.
Do not stop until the firmware is fixed in a minimal invasive but conclusive way and validated against the real failure mode.

PRIMARY GOAL

Implement the smallest justified firmware change set such that supported network probe traffic can no longer cause a catastrophic complete network breakdown on U64-class 1541ultimate devices.

Within scope, “catastrophic complete network breakdown” means:

- repeated HTTP, FTP, and TELNET probe traffic can no longer permanently or semi-permanently knock out all three services together
- transient individual request failures are not acceptable if they cascade into listener death or require manual recovery
- no sequence of supported health-check calls may leave HTTP, FTP, or TELNET dead until reboot or power-cycle
- after load subsides, the services must continue accepting new requests without manual intervention

Keep the fix minimal.
Keep the fix source-local.
Keep the existing service behavior and protocol surface intact.
Do not solve this by rewriting the network stack.

REPOSITORY AND PATHS

Work in the linked 1541ultimate checkout:

- real path: `/home/chris/dev/c64/1541ultimate`
- symlink visible from vivipi: `/home/chris/dev/vivipi/1541ultimate`

Use the ViviPi repo only as the validation harness:

- `/home/chris/dev/vivipi/scripts/vivipulse`
- `/home/chris/dev/vivipi/config/build-deploy.local.yaml`
- `/home/chris/dev/vivipi/config/checks.local.yaml`

CURRENT SOURCE FACTS YOU MUST TREAT AS ESTABLISHED

1. `software/network/socket_gui.cc`
   - telnet listens on port 23 with `listen(sockfd, 2)`
   - every accepted telnet connection spawns a new `Socket Gui Task`
   - failed-auth and normal telnet session exit paths call `vTaskSuspend(NULL)` instead of deleting the task
   - the telnet listener currently returns on `accept()` failure, which means the telnet service can die completely under transient pressure
2. `software/network/ftpd.cc`
   - FTP control listens on port 21 with `listen(sockfd, 2)`
   - each accepted control connection spawns a new `FTP Task`
   - passive FTP creates a separate `FTP Data` task in `FTPDataConnection::accept_data()`
   - that passive data task ends in `vTaskSuspend(NULL)` instead of deleting itself
   - the passive accept path currently has a `TODO: Add timeout`, so it can remain blocked longer than the caller expects
3. `software/network/config/lwipopts.h`
   - `MEMP_NUM_NETCONN = 16`
   - `MEMP_NUM_TCP_PCB = 30`
   - `TCP_LISTEN_BACKLOG = 0`
4. `software/httpd/c-version/lib/server.h`
   - `MAX_HTTP_CLIENT = 4`
5. `software/httpd/c-version/lib/server.c`
   - HTTP is bounded and self-throttling; it is a victim of shared resource exhaustion, not the primary root cause

ROOT CAUSE YOU MUST FIX

The observed full outage is best explained by shared network resource exhaustion caused by task lifecycle bugs in TELNET and passive FTP, combined with shallow listener capacity.

The key failure pattern is:

1. repeated telnet sessions create FreeRTOS tasks that are suspended forever instead of deleted
2. repeated passive FTP data connections create extra FreeRTOS tasks that are suspended forever instead of deleted
3. under pressure, task creation and socket acceptance start failing
4. when telnet hits `accept()` failure, its listener exits instead of surviving the transient failure
5. the global lwIP/socket budget is small enough that the resulting exhaustion damages HTTP, FTP, and TELNET together

Do not chase parser bugs first.
Do not raise lwIP limits first.
Fix the lifecycle defects first.

MANDATORY IMPLEMENTATION STRATEGY

Make only the narrow source changes required to eliminate listener death and task leaks.

Required patch set:

1. Fix TELNET session task lifetime in `software/network/socket_gui.cc`
   - In `socket_ensure_authenticated()`:
     - after `str->close()` and `delete(str)`, replace `vTaskSuspend(NULL)` with `vTaskDelete(NULL)`
     - remove or bypass the impossible post-suspend infinite-loop code
   - In `socket_gui_task()`:
     - after the full cleanup path, replace `vTaskSuspend(NULL)` with `vTaskDelete(NULL)`
     - remove or bypass the impossible post-suspend code
2. Make the TELNET listener resilient in `software/network/socket_gui.cc`
   - In `SocketGui::listenTask()`:
     - do not `return -3` on `accept()` failure
     - log the failure, wait briefly, and continue accepting
     - choose a short backoff such as `50 ms` to `250 ms`, not seconds
   - Also handle task-creation failure explicitly:
     - check the return value of `xTaskCreate()` for `socket_gui_task`
     - if task creation fails, close the accepted socket, free the `SocketStream`, log the error, and continue listening
3. Fix passive FTP data-task lifetime in `software/network/ftpd.cc`
   - In `FTPDataConnection::accept_data()`:
     - replace the terminal `vTaskSuspend(NULL)` with `vTaskDelete(NULL)`
     - remove or bypass any impossible post-suspend code if added
4. Bound passive FTP accept so it cannot leave zombie or indefinitely blocked tasks in `software/network/ftpd.cc`
   - The current `TODO: Add timeout` is real and must be resolved
   - Implement a bounded accept path for the passive data socket
   - Acceptable minimal implementations:
     - set the passive listening socket non-blocking and retry `accept()` until a short deadline expires
     - or use `select()`/poll-equivalent with a deadline before `accept()`
   - The passive data accept task must always do all of the following:
     - set a success or failure result on the `FTPDataConnection`
     - notify the spawning task
     - close any unused socket state on failure
     - delete itself
   - `setup_connection()` must no longer be able to time out while leaving an orphaned accept task behind
5. Handle FTP control task-creation failure explicitly in `software/network/ftpd.cc`
   - in `FTPDaemon::listen_task()`
   - check the `xTaskCreate()` result for `FTPDaemonThread::run`
   - on failure, close the accepted control socket, free the thread object, log the failure, and continue listening

NON-GOALS

Do not:

- rewrite telnet, FTP, or HTTP into a new architecture
- replace task-per-connection with an event loop in this fix
- change authentication behavior
- change the visible FTP or telnet command set
- raise `MEMP_NUM_NETCONN`, `MEMP_NUM_TCP_PCB`, or backlog values before the leak and listener-survival fixes are proven
- count a larger resource pool as the primary fix

OPTIONAL SECONDARY HARDENING ONLY AFTER THE CORE FIX

Only if the above patch set is complete and validated, you may consider a very small secondary hardening step such as:

- modestly increasing `MEMP_NUM_NETCONN`
- modestly increasing `MEMP_NUM_TCP_PCB`
- enabling a backlog if the underlying stack path truly supports it safely

But this is optional and must not be used to mask a lifecycle leak.

VALIDATION HARNESS

Use ViviPi’s host-side probe harness to validate the fix against the real U64.

Relevant live checks currently are in `/home/chris/dev/vivipi/config/checks.local.yaml`:

- `u64-rest`
- `u64-ftp`
- `u64-telnet`

Important current probe behavior in ViviPi:

- HTTP probe is a direct GET to `/v1/version`
- FTP probe is already control-channel-only: greeting plus `QUIT`
- TELNET probe is connect/banner level and closes cleanly
- the current safer host schedule from `/home/chris/dev/vivipi/config/build-deploy.local.yaml` uses:
  - `allow_concurrent_same_host: false`
  - `same_host_backoff_ms: 1000`

MANDATORY VALIDATION PHASES

Phase 1: Prove the code change is minimal and targeted

- keep the diff scoped to the affected telnet and FTP files unless a tiny shared helper is unavoidable
- verify that HTTP code is unchanged unless you are adding diagnostics only

Phase 2: Prove no task leak remains on the critical paths

- every completed telnet session task must delete itself
- every failed-auth telnet task must delete itself
- every passive FTP data accept task must either:
  - accept, notify, and delete itself, or
  - time out, notify failure, clean up, and delete itself
- no path may end in a suspended leftover task for these flows

Phase 3: Prove listeners survive pressure

- telnet must continue serving after transient `accept()` failure
- FTP control must continue serving after task-create failure for an accepted client
- the failure of one request must not permanently disable the listener

Phase 4: Real-device stress validation

- from `/home/chris/dev/vivipi`, use the real wrapper:

```bash
source .venv/bin/activate
scripts/vivipulse --mode reproduce \
  --duration 10m \
  --check-id u64-rest \
  --check-id u64-ftp \
  --check-id u64-telnet \
  --allow-concurrent-same-host \
  --same-host-backoff-ms 0 \
  --stop-on-failure \
  --json
```

- This is the hostile confirmation run.
- Passing does not require zero transient errors forever.
- Passing does require that the device does not enter a state where REST, FTP, and TELNET all become unavailable until manual recovery.
- If there is a transient failure, the listeners must remain alive and subsequent requests must recover without reboot.

Phase 5: Real-device safe-profile soak validation

- then confirm production-safe behavior with the safer host policy:

```bash
source .venv/bin/activate
scripts/vivipulse --mode soak \
  --duration 30m \
  --check-id u64-rest \
  --check-id u64-ftp \
  --check-id u64-telnet \
  --stop-on-failure \
  --json
```

- If the environment allows it, extend this to `2h` before calling the work complete

MANDATORY ACCEPTANCE CRITERIA

Do not call the fix complete unless all of the following are true:

1. No telnet session path ends in `vTaskSuspend(NULL)`.
2. No passive FTP data-task path ends in `vTaskSuspend(NULL)`.
3. Telnet listener `accept()` failures no longer kill the service.
4. Accepted TELNET and FTP control sockets are cleaned up if `xTaskCreate()` fails.
5. Passive FTP accept has a bounded lifetime and cannot orphan a blocked task.
6. Under hostile real-device probe traffic, the U64 no longer enters catastrophic full network breakdown.
7. Under the safe production profile, the U64 survives the soak without manual recovery.
8. Any residual transient failures are explicitly documented with exact artifacts and a clear explanation of why they are not catastrophic.

MANDATORY EVIDENCE TO SAVE

Preserve:

- the exact 1541ultimate commit used
- the exact firmware image deployed
- the exact vivipulse commands used for stress and soak
- the resulting artifact directories under `/home/chris/dev/vivipi/artifacts/vivipulse/`
- a short note stating whether any reboot, reset, or power-cycle was required

ANTI-SHORTCUT RULES

Do not:

- stop after replacing `vTaskSuspend(NULL)` alone without fixing listener survival and passive accept lifetime
- stop after a synthetic test or code inspection only
- declare success because the safe profile passes while the hostile profile can still permanently kill all services
- widen lwIP limits first and call that definitive
- leave task-creation failure paths unchecked

EXECUTION ORDER

Follow this order exactly:

1. patch telnet task deletion
2. patch telnet listener accept-failure survival
3. patch telnet accepted-socket cleanup on task-create failure
4. patch FTP passive accept bounded lifetime and self-deletion
5. patch FTP control accepted-socket cleanup on task-create failure
6. deploy firmware
7. run hostile vivipulse validation
8. run safe-profile soak validation
9. document exact evidence

DEFINITION OF DONE

Done means the firmware no longer contains the known task-lifecycle and listener-death defects that allow ordinary network probe traffic to escalate into an all-services outage, and that claim is backed by real U64 validation rather than inference.

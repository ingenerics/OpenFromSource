# CLI Contracts: Package Download

## `package_mgmt download`

Downloads every pending file from the configured mirrors into the cache pool,
verifying each against the cached archive index's checksum before it becomes
visible, and marks records verified. Admin-only. No arguments.

```text
package_mgmt download
```

### Progress (FR-014)

On a terminal, each phase (binary, then source) renders an elapsed-time line
with a progress bar beneath it, refreshed in place:

```text
Elapsed: 00:03:42
[██████████████████░░░░░░░░░░░░░░░░░░░░] 342/1200 files (1.2/4.5 GiB)
```

When stdout is not a terminal (piped/logged), the bar degrades to one line per
completed file (or a line every few seconds for very large runs):

```text
downloaded 342/1200 files (1.2/4.5 GiB)
```

The source phase renders its own elapsed line + bar when it has work; a phase
with nothing to do prints a single line instead (e.g. `Source phase: nothing
to download.`).

### Phase summaries

Each phase ends with a detailed summary block; the final block is followed by
a total:

```text
Binary phase summary
────────────────────
Files downloaded:  342
Bytes:             1.2 GiB
Files verified:    342
HTTP statuses:     200:342
Network errors:    0
Failed:            0
Elapsed:           00:03:42

Source phase summary
────────────────────
Files downloaded:  12
Bytes:             45.2 MiB
Files verified:    12
HTTP statuses:     200:12 503:1 429:1
Network errors:    1
Failed:            1
Elapsed:           00:00:31

Total: 354 files downloaded, 354 verified, 0 failed (00:04:13)
```

`HTTP statuses` is the per-phase histogram of every response status the
opener observed, one count per request attempt (`200:342` means 342 HTTP 200
responses) — including followed redirects and each retried attempt — sorted
by status code. `Network errors` counts requests that failed without any HTTP
status (connection refused, DNS, timeout, mid-transfer reset). Together they
distinguish mirror throttling (`429`/`503`) and server errors from plain
network latency in both the stdout block and the concise per-phase log line.

Failures are listed after the counts, capped at 50 on the console with the
full list in the log (same convention as `add --file` / `ensure-base`):

```text
Failed:            3
  - pool/main/v/vifm/vifm_1.0-2_amd64.deb: checksum mismatch (expected 8a3f…, got 91de…)
  - pool/main/l/lib1/lib1_2.0-1.dsc: HTTP 404
  - pool/main/l/lib2/lib2_3.0-1_amd64.deb: 5 retries exhausted (HTTP 503)
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | All pending files downloaded and verified (including zero pending) |
| 1 (`EXIT_ERROR`) | One or more files failed |
| 101 (`EXIT_SCHEMA_MISMATCH`) | Stale/missing database — rebuild instructions on stderr (spec 010 FR-012) |
| 102 (`EXIT_CONFIG_ERROR`) | Invalid configuration (existing behavior) |

### Error cases (JSON to stderr via the shared `_print_error` convention)

Per-file failures are reported in the summary block (above), not as JSON.
Command-level errors use JSON:

- Unconfirmable record (missing index entry/checksum for a record) →
  `{"error": "No index entry for trixie/main vifm 1.0-2; restore or re-fetch the cached index", "exit_code": 1}`
- Missing cached index for a release/component →
  `{"error": "Cached index missing for trixie/main; the index is part of the cache — restore or re-fetch it", "exit_code": 1}`

The summary block is printed to stdout exactly once per phase. The log
(Constitution VIII traceability) receives a concise per-phase line plus one
line per failure — the block itself is not re-logged, so terminal output
never duplicates the rows:

```text
download binary phase: 342 files downloaded, 342 verified, 0 failed
```

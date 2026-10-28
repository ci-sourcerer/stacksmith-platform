# Performance changes and measurements

The October 28, 2026 changes reduce repeated work in GitHub requests, worker reconciliation, run history, and Composer diagrams. Execution concurrency and polling intervals are unchanged.

## Runtime behavior

GitHub user, installation, and OAuth requests share an aiohttp session owned by the API or supervisor process. A lazily started AnyIO event-loop thread bridges the existing synchronous callers. The connector permits 20 concurrent connections, with a 30-second keep-alive timeout and a 20-second total request timeout. Cookies are disabled and authentication headers remain request-specific. Installation credentials and authorization checks remain fresh. Shutdown closes the session and joins the thread. The configured CA bundle and environment proxy settings are respected.

Worker reconciliation still renews leases, checks cancellation and deadlines, redacts logs, and collects terminal results. It only writes execution observations or log JSON when those values change. An unchanged log poll no longer advances the run's `updated` timestamp, which also avoids triggering the browser's artifact refresh.

Run-history queries select scalar summary columns directly. They no longer load specifications, logs, reports, or private execution metadata. The list response contract is documented in [the API guide](api.md); individual-run responses retain full details. Deploy the API and frontend together when adopting this contract.

Composer builds incoming and outgoing port indexes once per component/catalog/relationship change and route indexes once per saved-route change. Moving or selecting a node reuses its port data. Indexes retain only the current diagram. Existing port order, route precedence, and editing behavior are preserved.

## Local synthetic measurements

These measurements compare the previous implementation at commit `7f07b8b` with the changed implementation on the same development machine. They are microbenchmarks, not production service latency or full browser rendering measurements.

| Workload | Before median | After median | Before CPU per call | After CPU per call |
| --- | ---: | ---: | ---: | ---: |
| GitHub-shaped request to a local HTTP server | 3.13 ms | 0.39 ms | 3.23 ms | 0.32 ms |
| Silent worker tick with one active execution | 2.82 ms | 2.27 ms | 2.76 ms | 2.22 ms |
| History selection and serialization, 100 runs | 79.11 ms | 3.93 ms | 73.76 ms | 3.96 ms |
| Diagram data preparation, 100 nodes and 200 edges/routes | 7.17 ms | 0.16 ms | 7.42 ms | 0.23 ms |
| Diagram data preparation, 500 nodes and 1,000 edges/routes | 165.88 ms | 0.73 ms | 166.01 ms | 1.03 ms |

The HTTP comparison used 30 warmed sequential requests, reducing connections from 30 to one. It excludes remote-network latency and TLS handshakes. A separate concurrent test verified distinct authorization headers, no cookie sharing, and at most 20 connections for 48 requests from 24 callers.

The worker comparison used 50 timed ticks plus three warmups and one allocation sample on an isolated SQLite fixture with fake execution and artifact adapters. Across those 54 ticks, execution/log JSON updates fell from 108 to zero; lease and heartbeat writes continued.

The history comparison used 20 timed calls plus three warmups and one allocation sample. Each run contained a 64 KiB combined log, a 64 KiB phase log, and a 64 KiB report. The same authorized query scope and 100-item page were used for both versions. Serialized output fell from 19,757,948 bytes to 25,448 bytes. Peak traced Python allocations for one warmed call fell from 59,621,418 bytes to 155,546 bytes. PostgreSQL query plans and production latency were not measured.

Diagram measurements used 30 timed preparations after five warmups, including port definitions and saved-route lookups. They exclude React rendering, layout, and DOM work. The optimized case rebuilt the indexes on every sample; actual movement additionally benefits from memoized port data.

Peak traced allocations for a warmed HTTP request increased from 213,804 bytes to 277,564 bytes. Worker traced allocations were approximately unchanged. These allocation measurements exclude native allocations and total process RSS. The additional runtime resources are the bounded connection pool and one lazily created event-loop thread per process; whole-service memory under production traffic remains to be profiled.

## Regression coverage

Backend tests cover connection reuse, concurrent credential isolation, disabled cookies, pool limits, cleanup after failures, CA configuration, network errors, fresh installation tokens, OAuth errors, summary payload/query shape, and unchanged worker writes with continued lease renewal. Existing lifecycle, cancellation, redaction, and pagination tests remain in the suite.

Frontend tests cover port ordering and deduplication, first-route precedence, removal of stale components and edges, and stable node data during movement and selection. Existing interaction, navigation, history, and undo/redo tests remain in the suite.

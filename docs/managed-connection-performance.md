# Managed connection performance comparison

Measured on October 28, 2026 against the local Compose stack on port 8000.

## Method

The baseline used the repository's `HEAD` implementations of review handling, GitHub discovery, and worker scheduling. The comparison used the updated implementations with a 0.25-second inspection polling interval. Both runs used the same API, worker, PostgreSQL, execution image, authenticated session, signed stack snapshot, and configuration.

Each run alternated a policy description between a temporary value and its original value 20 times, waiting for managed connection inspection to complete after each edit. The normal editor debounce and automatic source preview remained enabled. Each run completed 20 reviews and 10 separate source-authoring inspections successfully. The original description and updated runtime were restored afterward. The saved draft recovery prompt remained active, preventing benchmark changes from overwriting the saved draft.

Review durations are API request handling times from logs, excluding the editor's 400 ms debounce and browser automation overhead. Queue durations are the review inspection's existing logged queue measurements. The p95 uses the nearest-rank method, selecting the nineteenth sorted value from 20 samples.

Temporary API logging counted GitHub transport requests without recording URLs, payloads, or credentials. GitHub and PostgreSQL totals cover each complete measurement window, including automatic source previews and background activity. CPU usage comes from container CPU counter deltas, divided by the window duration and expressed as a percentage of one CPU core. Database transactions are committed plus rolled-back transactions from `pg_stat_database`, rather than individual SQL statement counts.

## Latency and request counts

| Measurement | Baseline | Updated | Change |
| --- | ---: | ---: | ---: |
| Successful reviews | 20 | 20 | All succeeded |
| Median review time | 10.34 s | 8.04 s | 22% lower |
| p95 review time | 13.24 s | 10.75 s | 19% lower |
| Maximum review time | 14.54 s | 14.78 s | One slower outlier remains |
| Median queue wait | 0.681 s | 0.166 s | 76% lower |
| p95 queue wait | 2.901 s | 1.455 s | 50% lower |
| API GitHub requests per window | 449 | 307 | 32% fewer |
| Median catalog inspection | 2.48 s | 2.45 s | Essentially unchanged |
| Median association resolution | 0.013 s | 0.013 s | Essentially unchanged |
| Median inspection runtime | 3.54 s | 3.58 s | Essentially unchanged |

## Resource cost

| Measurement | Baseline | Updated |
| --- | ---: | ---: |
| Measurement window | 367.52 s | 294.57 s |
| Database transactions | 3,842 | 8,460 |
| Database transactions per second | 10.45 | 28.72 |
| API CPU, percentage of one core | 3.75% | 3.83% |
| Worker CPU, percentage of one core | 6.38% | 8.48% |
| PostgreSQL CPU, percentage of one core | 1.06% | 1.73% |
| Ending API container memory | 248.6 MiB | 248.9 MiB |
| Ending worker container memory | 126.1 MiB | 128.3 MiB |
| Ending PostgreSQL container memory | 53.2 MiB | 61.4 MiB |

Faster polling reduces review latency while increasing database and runtime-status traffic. Operators can raise `STACKSMITH_PLATFORM_INSPECTION_POLL_INTERVAL`, or Helm's `inspectionPollInterval`, if reducing polling load matters more than update latency. Memory measurements include container cache usage and are endpoint observations rather than peak measurements.

This is a small sequential comparison on one development stack. The different measurement window lengths include automation and idle gaps, so resource rates describe these complete windows rather than a controlled saturation test. Startup and completion variability remains visible in the maximum duration. These results do not establish production or Kubernetes latency guarantees.

## Validation

`poe format` and `poe lint` passed. The complete `poe test` run passed with 553 tests and 15 skipped tests. The new tests cover targeted discovery, pagination, custom properties, access revocation during inspection, configuration changes, independent polling cadences, supervisor intervals, and deployment settings. The local API returned HTTP 200 from `/api/ready` after temporary benchmark instrumentation was removed.

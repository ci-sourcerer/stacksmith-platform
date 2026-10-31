# Editing contexts

Composer prepares component metadata and managed connection rules in an isolated inspection when a context is first needed. Eligible edits then use the pinned Stacksmith library in the API to author the draft and resolve associations. Moving components does not request another connection review. Source rendering uses the authoring library and remains available when inspection fails.

The context key includes the principal, repository snapshot, configuration and dependency revisions, environment revision, execution image, and resolver version. Prepared metadata is encrypted in PostgreSQL for 24 hours and retained in a bounded per-process cache. It contains no authored draft result. Configuration notifications wake connected editors and invalidate in-process metadata; reconnecting editors refresh their signed snapshot. Every review checks current access and relevant revisions, and pull requests perform a fresh isolated inspection.

Contexts with unsupported or unstable inputs fall back to isolated inspection. This includes multi-file environments, additional runfile configuration or variables, default module mappings, unstable dependencies, and templates other than supported deferred component references. Their edit latency is not established by the reference test.

## Database connections

Each API process uses a five-connection query pool by default and one separate notification listener. The worker, supervisor, and maintenance processes use one query connection each. Set `STACKSMITH_PLATFORM_DATABASE_LISTENER_URL` in the configuration secret when queries use transaction-pooled PgBouncer; the listener must use a direct PostgreSQL or session-pooled endpoint for the same database. Run `python -m scripts.database_budget --help` before increasing replica or job limits, including rollout surge and operational reserve.

The administrator-only `/api/editor-status` endpoint reports prepared contexts, listener connections and reconnects, PostgreSQL connections, query checkout waits and timeouts, GitHub request counters, and recent backend review latency. Backend latency excludes browser debounce and network transit.

## Rollout

Drain and stop any experimental inspection controllers before upgrading. Apply migration `0008`, rebuild the API and execution images, and restart the API and worker or Kubernetes supervisors. The migration removes disposable inspection-pool state and creates the encrypted prepared-context store. Existing deployment supervision remains separate.

## Compose capacity result

On the local 8 CPU Docker VM with 11.67 GiB available to the complete test stack, a ten-minute run used 15 distinct server principals backed by one approved read-only GitHub login. It included three source viewers, stack sizes from 1 to 25 components, edits every 20 to 60 seconds, and five simultaneous bursts. Among 302 loaded-stack edits, median latency was 2.46 seconds and p95 was 2.98 seconds including the 400 ms debounce. Initial-load p95 was 32.79 seconds and maximum edit latency was 3.54 seconds. There were no failed or incorrect accepted results, no inspection jobs during edits, no OOM events, and 80.2 percent measured memory headroom. Mean VM CPU was 4.8 percent. The run used 3,420 GitHub requests, including 3,306 conditional hits; the database had seven connections at the end and no API pool checkout timeouts.

## Kubernetes capacity result

On a separate local eight-vCPU Kubernetes profile, the same ten-minute workload produced 302 loaded edits with a 2.50-second median and 3.17-second p95 including debounce. Maximum edit latency was 3.91 seconds; initial-load p95 was 40.40 seconds. There were 15 initial inspection jobs, with a 24.76-second p95 queue wait, and no inspection jobs during editing. There were no failed or incorrect accepted results, no OOM events, and 53.9 percent measured memory headroom. Mean VM CPU was 7.0 percent. The run made 3,420 GitHub requests, including 3,316 conditional hits; the database had seven connections at the end and no API pool checkout timeouts. The execution network policy allowed the test storage Pod address because this CNI evaluates the policy after Service address translation.

Both runs establish results for the example AWS fixture and its direct-stack environment. They do not establish performance for distinct GitHub accounts or configurations that require the isolated fallback.

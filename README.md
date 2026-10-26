# AlertFlow

**Inspect a durable outbox state machine without sending a single message.**

AlertFlow uses SQLite to model queued delivery, idempotency keys, worker leases, exponential retry delays and dead letters. Its transport is a local receipt table. Synthetic success, failure and crash outcomes make otherwise difficult delivery scenarios reproducible.

## Run

Python 3.9+ · standard library only.

```sh
python3 main.py
python3 main.py --input example.json
python3 main.py --help
python3 -m unittest discover -s . -p 'test_main.py' -v
```

Default demo and help work from any working directory. The fixture is located beside the script. The CLI uses an in-memory database; `Outbox(database="/path/to/queue.sqlite")` uses a persistent local file. Explicit input paths follow the caller's working directory.

## Delivery lifecycle

`pending → processing → delivered` is the success path. A failed attempt returns to `pending` with delay `base_delay × 2^(attempts-1)`. The third failed attempt enters `dead`. A lease prevents another worker from claiming a processing item until it expires; the attempt number fences stale workers. Claiming and delivery validation take SQLite write locks before inspecting mutable state.

An idempotency key accepts the same canonical JSON payload repeatedly, but rejects reuse with a changed payload. The receipt table accepts each key once. `accepted_then_crash` records a receipt while leaving the outbox unacknowledged; after lease expiry, recovery observes that receipt and marks the message delivered without another acceptance. A final attempt whose expired lease has no receipt becomes a dead letter.

When reopening a persistent queue with a lower `max_attempts`, the next claim moves pending messages that have already reached that limit to `dead`. Active leases remain valid until they expire; reducing the limit does not cancel an in-flight delivery. Configure workers sharing a queue with the same retry limit.

The demo leaves `demo-a`, `demo-b` and `demo-d` delivered, and `demo-c` dead after three failures. Its payloads reference fictional document IDs; there are no addresses or external destinations.

## Tests and guarantees

Tests cover enqueue idempotency, payload conflicts, single receipt delivery, retry timing, dead-letter transition, crash recovery, stale claims, two-connection lease exclusion, retry-limit changes across restarts and schedule validation.

Receipt deduplication is a property of this **local simulator**. Real external transports require an idempotency contract or reconciliation to handle the crash between remote acceptance and local acknowledgment; the simulator does not prove exactly-once external delivery. Logical timestamps are supplied by the caller and must advance consistently. There is no background scheduler, notification provider, network connection or real message dispatch.

## Scope

This is a Python reference core; Java, Spring Batch and MySQL integration are not implemented. The local tests do not establish production throughput.

## Repository checks

```sh
python3 verify.py
```

This runs the isolated test suite, synthetic example, and CLI help. The included GitHub Actions workflow repeats these checks when the repository is published. No hosted CI result is claimed by the local verification report.

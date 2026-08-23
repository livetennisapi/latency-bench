"""Source adapters. Each exposes pure `parse_*` functions (offline-testable) and an async
`run(emit, ...)` coroutine that owns the network I/O. Nothing here is imported with a key."""

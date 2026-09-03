# Materialized State Tables in SQLite with Transactional Dual-Write

We need the Persistent Runtime to recover instantly across process restarts and crashes without replaying massive event logs. We decided to maintain authoritative materialized state tables (`scene_states`, `open_loops`, `tasks`) updated transactionally alongside raw events in a single SQLite database, rather than relying on pure event sourcing replay on startup. This gives us zero-lag crash recovery and fast point-lookups while preserving the full append-only event stream for auditing and replay.

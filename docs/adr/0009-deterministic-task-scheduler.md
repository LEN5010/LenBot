# In-Process Hybrid Min-Heap Task Scheduler with SQLite Sync

Background tasks in distributed bot architectures often rely on external brokers (Celery, Redis) or blunt polling loops. We decided to build an in-process hybrid scheduler using an in-memory `asyncio` priority queue/min-heap paired with a periodic (10s) SQLite synchronization sweep. When a task matures, the scheduler marks its state as `triggered` and emits an immutable `TASK_DUE` event directly into the ingest pipeline, preserving strict causal auditability without external middleware dependencies.

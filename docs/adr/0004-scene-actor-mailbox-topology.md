# Dedicated Worker Coroutine and Mailbox Router per Scene

To guarantee single-writer state mutations without blocking incoming events on LLM inference, each active Scene runs a dedicated `asyncio` worker consuming an isolated `asyncio.Queue`. The worker executes `SceneReducer.reduce(event)` synchronously and routes relevant in-flight events to the active `EpisodeMailbox` for steering. This achieves strict FIFO serialization and zero lock-contention overhead.

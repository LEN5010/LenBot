# V1-A Reactive Core Scope with Stubs for Memory and Scheduler

To rapidly validate the foundational hypothesis—that an autonomous agent can deterministically decide when to speak versus remain silent without persistent LLM sessions—we decided that V1-A will deliver the complete reactive pipeline (OneBot, EventStore, SceneActor, StimulusBuilder, Attention, EpisodeManager, PiCore, RuntimeGate, and ActionQueue) while stubbing long-term memory reflection and complex background task scheduling. This isolates and proves core execution semantics before adding background cognitive jobs.

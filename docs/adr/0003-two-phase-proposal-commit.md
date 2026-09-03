# Two-Phase Proposal Commit with Dependency Cascading

When an Episode produces multiple proposals (e.g. messages, tasks, open loops), network instability or OneBot rate limits may cause message delivery to fail. We decided that internal state proposals (such as background tasks) commit independently, whereas interaction-dependent states (such as waiting-for-reply Open Loops) only commit after external `MESSAGE_SENT` events confirm successful delivery. This prevents phantom open loops without discarding valid internal task scheduling.

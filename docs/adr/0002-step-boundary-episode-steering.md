# Step-Boundary Episode Steering with Runtime Gate Staleness Check

When user input or external events change the context of an in-flight cognitive episode, hard cancellation of async tasks or LLM streams risks dangling socket states and dirty tool side effects. We decided to inject steering signals at ReAct step boundaries and enforce a final Response Staleness check at the Runtime Gate, rather than killing in-flight tasks abruptly. This ensures clean tool execution and guarantees that stale or superseded actions are safely committed as SILENCE.

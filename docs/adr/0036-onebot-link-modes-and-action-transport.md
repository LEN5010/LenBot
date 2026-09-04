# ADR-0036: Runtime-Owned OneBot Link Modes and Action Transport

- Status: Accepted
- Date: 2026-09-05

LenBot must work with both OneBot deployments that connect to a bot-owned reverse WebSocket and deployments that expose a Universal WebSocket for the bot to connect to. `OneBotAdapter` therefore owns one persisted **OneBot Link** with an explicit connection mode (`reverse_ws` or `forward_ws`) and an independently selected action transport (`websocket` or `http`). Forward WebSocket disconnects reconnect with bounded exponential backoff; outstanding echo requests fail immediately when their connection closes. Outbound actions are attempted exactly once on the selected transport—LenBot never falls back from WebSocket to HTTP after an ambiguous send, because that could duplicate a user-visible message.

The Control Plane may update and persist link settings, after which the runtime restarts the adapter. Access tokens are write-only in the API and are attached as `Authorization: Bearer` for both transports. WebSocket remains the event source in every mode; HTTP is only an optional action and health-check transport, so plugins and adapters still cannot bypass `Event → Runtime State` or `ActionQueue`.

import pytest

from len_bot.actions.models import ActionItem, ActionType, DeliveryResult, DeliveryStatus
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.testing.replay import drain
from len_bot.testing.social import social_result


SCENE = "group:126300994"


async def silent(messages):
    return social_result(reason="isolated fixture")


def action(content, scene=SCENE, **kwargs):
    return ActionItem(action_type=ActionType.SEND_GROUP_MESSAGE, scene_id=scene, content=content, **kwargs)


@pytest.mark.asyncio
async def test_shadow_and_scene_selection_control_delivery_and_persist(tmp_path):
    sent = []

    async def send(item):
        sent.append(item.content)
        return DeliveryResult(status=DeliveryStatus.SENT, transport="fake")

    async def respond(messages):
        return social_result(reason="回应当前真人消息", content="fresh-response")

    config = RuntimeConfig(db_path=str(tmp_path / "delivery.db"))
    runtime = AgentRuntime(config, send_adapter=send, mock_social_handler=respond)
    await runtime.start()
    try:
        assert runtime.shadow_mode
        assert runtime.allowed_scenes == {SCENE}
        runtime.action_queue.enqueue(action("global-shadow"))
        await runtime.action_queue._queue.join()
        assert sent == []

        await runtime.set_shadow_mode(False)
        await runtime.receive_event(Event(
            event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id=SCENE,
            actor_id="user:1", payload={"raw_text": "在吗", "at_bot": True},
        ))
        await drain(runtime)
        assert sent == ["fresh-response"]
        runtime.action_queue.enqueue(action("shadow-origin", origin_mode="shadow"))
        runtime.action_queue.enqueue(action("outside-list", scene="group:2"))
        runtime.action_queue.enqueue(action("live"))
        await runtime.action_queue._queue.join()
        assert sent == ["fresh-response", "live"]

        await runtime.set_delivery_scenes(["group:2"])
        runtime.action_queue.enqueue(action("removed-scene"))
        runtime.action_queue.enqueue(action("new-scene", scene="group:2"))
        await runtime.action_queue._queue.join()
        assert sent == ["fresh-response", "live", "new-scene"]
    finally:
        await runtime.stop()

    restarted = AgentRuntime(config, mock_social_handler=silent)
    await restarted.start()
    try:
        assert restarted.allowed_scenes == {"group:2"}
        assert restarted.shadow_mode is False
    finally:
        await restarted.stop()


@pytest.mark.asyncio
async def test_unknown_delivery_stops_only_its_batch(tmp_path):
    sent = []

    async def send(item):
        sent.append(item.content)
        return DeliveryResult(
            status=DeliveryStatus.UNKNOWN if item.content == "ambiguous" else DeliveryStatus.SENT,
            transport="fake",
        )

    runtime = AgentRuntime(
        RuntimeConfig(db_path=str(tmp_path / "batch.db"), message_pacing=False),
        send_adapter=send,
        mock_social_handler=silent,
    )
    await runtime.start()
    try:
        await runtime.set_shadow_mode(False)
        for index, content in enumerate(["ambiguous", "must-not-send"]):
            runtime.action_queue.enqueue(action(content, batch_id="failure", batch_index=index, batch_size=2))
        runtime.action_queue.enqueue(action("next-batch", batch_id="next"))
        await runtime.action_queue._queue.join()
        assert sent == ["ambiguous", "next-batch"]
        assert runtime.shadow_mode is False
        assert runtime.allowed_scenes == {SCENE}
        actor = await runtime.scene_manager.get_or_create_actor(SCENE)
        await actor._queue.join()
        events = await runtime.event_store.get_recent_events(SCENE)
        failure = next(event for event in events if event.payload.get("content") == "ambiguous")
        assert failure.event_type == EventType.MESSAGE_SEND_FAILED
        assert failure.payload["delivery_status"] == "unknown"
    finally:
        await runtime.stop()

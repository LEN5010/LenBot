"""Withdraw staged intent without cancelling or inventing committed work."""

import pytest

from len_bot.cognition.agent_loop import TerminalArgumentError, ToolArgumentError
from len_bot.cognition.context import ConversationContext
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.cognition.proposals import ProposalLedger
from len_bot.cognition.social_core import SocialCognitionCore
from test_conversation_agent import harness, call, response, text_reply


@pytest.mark.asyncio
async def test_new_cancellation_discards_staged_work_before_single_terminal_commit(harness):
    h = harness
    source = await h.human("帮我查一下A", "original")
    requests = await h.setup([
        response(call("start_work", {"proposal_ref": "lookup", "goal": "核对A", "evidence": ["M1"]})),
        response(call("discard_proposal", {"proposal_ref": "lookup"}, "discard"),
                 call("finish_turn", text_reply("好，不查了"), "finish")),
    ])
    snapshot = h.actor.session.model_copy(deep=True)
    cutoff = snapshot.last_observed_event_rowid
    sources = [source.id]
    mailbox = EpisodeMailbox("steered", h.actor.scene_id, snapshot.version)
    mailbox.acknowledge_through(cutoff)
    assert h.actor.acquire_episode_lease(mailbox.episode_id, mailbox)
    observed = []

    async def observe():
        nonlocal cutoff
        cancelled = await h.human("不用查了，取消", "cancel-request")
        observed.append(cancelled)
        cutoff = h.actor.session.last_observed_event_rowid
        sources.append(cancelled.id)
        mailbox.acknowledge_through(cutoff)
        return {"session": h.actor.session.model_copy(deep=True), "events": [cancelled],
                "through_rowid": cutoff, "source_event_ids": list(sources)}

    async def commit(outcome):
        # Use the production Actor/Gate binding from the harness through the
        # original run's closure-free Gate fixture.
        from len_bot.runtime.gate import RuntimeGate
        from types import SimpleNamespace
        gate = RuntimeGate(h.store, SimpleNamespace(enqueue=h.actions.append), bot_actor_id="user:99")
        return await h.actor.commit_turn(outcome, cutoff, sources, snapshot.knowledge_revision, mailbox, gate)

    trace = {}
    try:
        result = await SocialCognitionCore(h.runtime).run(snapshot, [source], cutoff, mailbox.episode_id,
            sources, observe=observe, commit=commit, trace=trace)
    finally:
        h.actor.release_episode_lease(mailbox.episode_id)
    assert len(requests) == 2 and len(observed) == 1
    assert "不用查了" in str(requests[1]["messages"])
    assert result.job_proposals == [] and await h.store.list_jobs(h.actor.scene_id) == []
    assert [action.content for action in h.actions] == ["好，不查了"]
    assert h.actor.session.last_cognized_event_rowid == observed[0].metadata["_rowid"]
    assert trace["tool_calls_used"] == 2


@pytest.mark.asyncio
async def test_parameter_repair_can_discard_prior_staging_without_any_side_effect(harness):
    h = harness
    await h.human("帮我核对资料")
    await h.setup([
        response(call("start_work", {"proposal_ref": "lookup", "goal": "核对资料", "evidence": ["M1"]}, "stage"),
                 call("finish_turn", {"messages": [{"segments": [{"type": "image", "asset_id": "unknown"}]}]}, "bad")),
        response(call("discard_proposal", {"proposal_ref": "lookup"}, "discard"),
                 call("finish_turn", {"messages": []}, "finish")),
    ])
    result, trace = await h.run()
    assert len(trace["contract_repairs"]) == 1
    assert result.job_proposals == [] and not h.actions
    assert await h.store.list_jobs(h.actor.scene_id) == []


@pytest.mark.asyncio
async def test_discard_is_per_proposal_and_silence_preserves_other_staged_work(harness):
    h = harness
    event = await h.human()
    context = ConversationContext(h.runtime, h.actor.session, event.metadata["_rowid"])
    context.event_message(event)
    ledger = ProposalLedger(context, "ledger")
    await ledger.stage("start_work", {"proposal_ref": "first", "goal": "A", "evidence": ["M1"]})
    await ledger.stage("start_work", {"proposal_ref": "second", "goal": "B", "evidence": ["M1"]})
    receipt = await ledger.stage("discard_proposal", {"proposal_ref": "first"})
    assert receipt["status"] == "discarded"
    result = await ledger.finish({"messages": []})
    assert [job.proposal_id for job in result.job_proposals] == ["second"]
    with pytest.raises(TerminalArgumentError, match="ack_ref"):
        await ledger.finish({"messages": [{"segments": [{"type": "text", "text": "已经安排了"}], "ack_ref": "first"}]})


@pytest.mark.asyncio
async def test_non_creation_staging_returns_handles_and_withdraws_exact_entries(harness):
    h = harness
    event = await h.human("叫我小A")
    context = ConversationContext(h.runtime, h.actor.session, event.metadata["_rowid"])
    context.event_message(event)
    context.refs.register_job({"id": "existing-job", "revision": 2})
    context.refs.register_loop({"id": "existing-loop"})
    ledger = ProposalLedger(context, "ledger")
    belief = await ledger.stage("remember", {"subject": "U1", "kind": "address", "statement": "A希望被叫小A", "evidence": ["M1"]})
    control = await ledger.stage("cancel_work", {"work_ref": "J1", "evidence": ["M1"]})
    wait = await ledger.stage("resolve_wait", {"wait_ref": "L1"})
    again = await ledger.stage("resolve_wait", {"wait_ref": "L1"})
    assert again["proposal_ref"] == wait["proposal_ref"]
    assert len({item["proposal_ref"] for item in (belief, control, wait)}) == 3
    for item in (belief, control, wait):
        await ledger.stage("discard_proposal", {"proposal_ref": item["proposal_ref"]})
    result = await ledger.finish({"messages": []})
    assert result.memory_proposals == result.job_proposals == result.resolve_open_loop_ids == []
    assert context.refs.job("J1")["revision"] == 2
    assert context.refs.loop_id("L1") == "existing-loop"


@pytest.mark.asyncio
async def test_committed_references_and_repeated_discards_are_not_pending_handles(harness):
    h = harness
    event = await h.human()
    context = ConversationContext(h.runtime, h.actor.session, event.metadata["_rowid"])
    context.event_message(event)
    context.refs.register_job({"id": "existing-job", "revision": 2})
    ledger = ProposalLedger(context, "ledger")
    with pytest.raises(ToolArgumentError, match="已提交"):
        await ledger.stage("discard_proposal", {"proposal_ref": "J1"})
    await ledger.stage("start_work", {"proposal_ref": "lookup", "goal": "A", "evidence": ["M1"]})
    await ledger.stage("discard_proposal", {"proposal_ref": "lookup"})
    with pytest.raises(ToolArgumentError, match="未找到本轮暂存提案"):
        await ledger.stage("discard_proposal", {"proposal_ref": "lookup"})


@pytest.mark.asyncio
async def test_relative_reminder_uses_commit_clock_and_requires_exactly_one_time(harness):
    h = harness
    h.store.clock = lambda: 1000
    h.runtime.clock = lambda: h.store.clock()
    event = await h.human("20分钟后提醒我休息")
    context = ConversationContext(h.runtime, h.actor.session, event.metadata["_rowid"])
    context.event_message(event)
    ledger = ProposalLedger(context, "validate-reminder")
    base = {"proposal_ref": "break", "description": "提醒休息", "requester": "U1", "evidence": ["M1"]}
    for timing in ({}, {"due_at": 2200, "delay_seconds": 1200}, {"delay_seconds": 0}, {"delay_seconds": -1}):
        with pytest.raises(ToolArgumentError):
            await ledger.stage("schedule_reminder", {**base, **timing})
    assert ledger.tasks == []

    async def finish_later():
        h.store.clock = lambda: 5000
        return response(call("finish_turn", {"messages": []}))

    await h.setup([response(call("schedule_reminder", {**base, "delay_seconds": 1200})), finish_later])
    outcome, _ = await h.run()
    assert outcome.task_proposals[0].due_at is None
    assert outcome.task_proposals[0].delay_seconds == 1200
    tasks = await h.store.scene_tasks(h.actor.scene_id)
    assert len(tasks) == 1 and tasks[0]["due_at"] == 6200

"""One model request, shared by chat turns and explicit plugin generation."""

from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from typing import Literal, Protocol

from ..config import SharedConfig
from ..chat.recap import CompactionPlan, ContextBudgetError, estimate_text_request
from .client import ChatModel, ModelProtocolError, ModelReply, parse_token_usage
from .slots import ModelSlots
from .projection import project_messages
from urllib.parse import quote
from .tokens import token_record
from ..storage.store import Store
from ..plugins.store import PluginStore


class ChatRequest(Protocol):
    """The scene request callback used by expression and image tools."""

    def __call__(self, turn_id: str, role: Literal["mind", "recap", "vision"],
                 messages: list[dict], tools: list[dict], *,
                 recap_target: CompactionPlan | None = None,
                 expression_ids: list[int] | None = None,
                 input_estimate: tuple[int, str] | None = None) -> Awaitable[ModelReply]: ...


def request_estimate(config: SharedConfig, store: Store, model: ChatModel, *, scene: str, role: str,
                     messages: list[dict], tools: list[dict], output_tokens: int) -> tuple[int, str]:
    """Actual prior input plus changed-material estimates, never cached-token discounts."""
    binding = getattr(config.models.roles, "mind" if role == "recap" else role)
    messages = project_messages(messages, binding.history_policy)
    estimated = estimate_text_request(messages, tools, output_tokens)
    images = any(isinstance(message.get('content'), list) and any(
        block['type'] == 'image_url' for block in message['content']) for message in messages)
    if images:
        return estimated, 'text_estimate_images_unknown'
    previous = store.last_mind_usage(scene) if role == 'mind' else None
    if previous is not None:
        request, usage = previous
        tokens = parse_token_usage(usage)
        prior = project_messages(request['messages'], binding.history_policy)
        prefix = prior[:-1]
        same_binding = (request['provider'] == config.models.roles.mind.provider
                        and request['settings'] == model.settings.model_dump(exclude={'api_key'})
                        and request.get('history_policy') == binding.history_policy)
        previous_images = any(isinstance(message.get('content'), list) and any(
            block['type'] == 'image_url' for block in message['content']) for message in request['messages'])
        if (tokens is not None and tokens.prompt_tokens is not None and same_binding
                and not previous_images and request['tools'] == tools
                and messages[:len(prefix)] == prefix):
            old_estimate = estimate_text_request(prior, tools, 0)
            estimated = max(0, tokens.prompt_tokens + estimated - output_tokens - old_estimate) + output_tokens
            return estimated, 'usage_assisted'
    return estimated, 'utf8_bytes_estimate'


async def request_model(config: SharedConfig, store: Store, model: ChatModel,
                        messages: list[dict], tools: list[dict], *, scene: str, role: str,
                        turn_id: str | None = None, plugin: str | None = None,
                        slots: ModelSlots | None = None, direct: bool = False,
                        output_tokens: int | None = None,
                        validate: Callable[[ModelReply], None] | None = None,
                        append_to_scene: bool = False, recap_for: tuple[str, int] | None = None,
                        expression_ids: list[int] | None = None,
                        input_estimate: tuple[int, str] | None = None,
                        notify: Callable[[], None] | None = None) -> ModelReply:
    binding = getattr(config.models.roles, "mind" if role == "recap" else role)
    messages = project_messages(messages, binding.history_policy)
    tokens = binding.max_output_tokens if output_tokens is None else output_tokens
    session_id = None
    if binding.history_policy == "omit-reasoning":
        session_id = quote(f"{config.database.resolve()}:{scene}:{role}", safe="")
    if input_estimate is None:
        estimated, method = request_estimate(config, store, model, scene=scene, role=role,
                                             messages=messages, tools=tools, output_tokens=tokens)
    else:
        input_tokens, method = input_estimate
        estimated = input_tokens + tokens
    if estimated > binding.context_window_tokens:
        scope = "文本部分" if role == "vision" else "请求"
        raise ContextBudgetError(
            f"{role} {scope}含预留输出估算 {estimated} token，超过配置窗口 {binding.context_window_tokens}；未调用模型")
    settings = {**model.settings.model_dump(exclude={"api_key"}), "max_output_tokens": tokens}
    async with (slots.slot(direct=direct, scene=scene) if slots is not None else nullcontext()):
        request = {"settings": settings, "messages": messages, "tools": tools, "provider": binding.provider,
                   "estimate_method": method,
                   **({"expression_ids": expression_ids} if expression_ids is not None else {}),
                   **({"estimated_text_tokens": estimated, "estimated_total_tokens": None} if role == "vision"
                      else {"estimated_total_tokens": estimated}),
                   "context_window_tokens": binding.context_window_tokens,
                   "history_policy": binding.history_policy, "session_id": session_id}
        call_id = (store.start_call(turn_id, role, request) if plugin is None
                   else PluginStore(store).start_plugin_call(scene, plugin, role, request))
        if notify is not None:
            notify()
        reply = None
        try:
            reply = await model.complete(messages, tools, max_output_tokens=tokens, session_id=session_id)
            if validate is not None:
                validate(reply)
        except BaseException as error:
            response = None if reply is None else {"message": reply.message, "finish_reason": reply.finish_reason, "raw": reply.response}
            usage = None if reply is None else reply.usage
            token_usage = None if reply is None else reply.token_usage
            if isinstance(error, ModelProtocolError):
                response, usage, token_usage = error.response, error.usage, error.token_usage
            store.end_call(call_id, response, usage, f"{type(error).__name__}: {error}",
                           tokens=token_record(token_usage))
            if notify is not None:
                notify()
            raise
        store.end_call(call_id, {"message": reply.message, "finish_reason": reply.finish_reason, "raw": reply.response}, reply.usage,
                       tokens=token_record(reply.token_usage),
                       append_to_scene=scene if append_to_scene else None, recap_for=recap_for)
        if notify is not None:
            notify()
        return reply

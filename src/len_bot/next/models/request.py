"""One model request, shared by chat turns and explicit plugin generation."""

from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from typing import Literal, Protocol

from ..config import SharedConfig
from ..chat.recap import CompactionPlan, ContextBudgetError, estimate_request, estimate_text_request
from .client import ChatModel, ModelProtocolError, ModelReply
from .slots import ModelSlots
from .pricing import estimate_cost
from ..storage.store import Store
from ..plugins.store import PluginStore


class ChatRequest(Protocol):
    """The scene request callback used by expression and image tools."""

    def __call__(self, turn_id: str, role: Literal["mind", "voice", "recap", "vision"],
                 messages: list[dict], tools: list[dict], *,
                 recap_target: CompactionPlan | None = None,
                 expression_ids: list[int] | None = None) -> Awaitable[ModelReply]: ...


async def request_model(config: SharedConfig, store: Store, model: ChatModel,
                        messages: list[dict], tools: list[dict], *, scene: str, role: str,
                        turn_id: str | None = None, plugin: str | None = None,
                        slots: ModelSlots | None = None, direct: bool = False,
                        output_tokens: int | None = None,
                        validate: Callable[[ModelReply], None] | None = None,
                        append_to_scene: bool = False, recap_for: tuple[str, int] | None = None,
                        expression_ids: list[int] | None = None,
                        notify: Callable[[], None] | None = None) -> ModelReply:
    binding = getattr(config.models.roles, "mind" if role == "recap" else role)
    tokens = binding.max_output_tokens if output_tokens is None else output_tokens
    estimate = estimate_text_request if role == "vision" else estimate_request
    estimated = estimate(messages, tools, tokens)
    if estimated > binding.context_window_tokens:
        scope = "文本部分" if role == "vision" else "请求"
        raise ContextBudgetError(
            f"{role} {scope}含预留输出估算 {estimated} token，超过配置窗口 {binding.context_window_tokens}；未调用模型")
    settings = {**model.settings.model_dump(exclude={"api_key"}), "max_output_tokens": tokens}
    price = config.models.prices.get(binding.provider, {}).get(binding.model)
    async with (slots.slot(direct=direct, scene=scene) if slots is not None else nullcontext()):
        request = {"settings": settings, "messages": messages, "tools": tools, "provider": binding.provider,
                   **({"expression_ids": expression_ids} if expression_ids is not None else {}),
                   "price": None if price is None else price.model_dump(mode="json"),
                   **({"estimated_text_tokens": estimated, "estimated_total_tokens": None} if role == "vision"
                      else {"estimated_total_tokens": estimated}),
                   "context_window_tokens": binding.context_window_tokens}
        call_id = (store.start_call(turn_id, role, request) if plugin is None
                   else PluginStore(store).start_plugin_call(scene, plugin, role, request))
        if notify is not None:
            notify()
        reply = None
        try:
            reply = await model.complete(messages, tools, max_output_tokens=tokens)
            if validate is not None:
                validate(reply)
        except BaseException as error:
            response = None if reply is None else {"message": reply.message, "finish_reason": reply.finish_reason}
            usage = None if reply is None else reply.usage
            token_usage = None if reply is None else reply.token_usage
            if isinstance(error, ModelProtocolError):
                response, usage, token_usage = error.response, error.usage, error.token_usage
            store.end_call(call_id, response, usage, f"{type(error).__name__}: {error}",
                           cost=estimate_cost(price, token_usage))
            if notify is not None:
                notify()
            raise
        store.end_call(call_id, {"message": reply.message, "finish_reason": reply.finish_reason}, reply.usage,
                       cost=estimate_cost(price, reply.token_usage),
                       append_to_scene=scene if append_to_scene else None, recap_for=recap_for)
        if notify is not None:
            notify()
        return reply

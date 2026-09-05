"""Explicit scene selection for runtimes with isolated fake transports."""


async def allow_fake_delivery(runtime, *scenes):
    assert runtime._onebot_adapter is None, "Fake delivery must not use a OneBot connection"
    runtime.allowed_scenes = set(scenes)
    await runtime.set_shadow_mode(False)

"""Small isolated helpers for the current fact-session runtime tests."""

from len_bot.cognition.providers import ModelProfile, ProviderConfig, RoutingConfig


async def configure_fixture_profile(runtime):
    profile = ModelProfile(provider_id="fixture", model="fixture-model", reasoning_effort="high")
    await runtime.provider_registry.apply_update([
        ProviderConfig(id="fixture", base_url="https://fixture.invalid/v1", api_key="isolated-fixture")],
        RoutingConfig(conversation=profile, work=profile))
    await runtime.event_store.save_dynamic_config("provider_config", runtime.provider_registry.export())


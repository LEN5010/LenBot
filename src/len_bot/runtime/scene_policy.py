"""Operator-owned group fields decide each real interaction's eligibility."""
from __future__ import annotations


class ScenePolicy:
    def __init__(self, config_store):
        self.config_store = config_store

    def scene(self, scene_id):
        return self.config_store.current.scenes.get(scene_id)

    def enabled(self, scene_id):
        if scene_id.startswith('private:'):
            return True
        scene = self.scene(scene_id)
        return bool(scene and scene.enabled)

    def chat_allowed(self, scene_id, requester_qq_uid):
        if scene_id.startswith('private:'):
            return True
        scene = self.scene(scene_id)
        if not scene or not scene.enabled:
            return False
        if scene.chat:
            return True
        return bool(requester_qq_uid is not None and int(requester_qq_uid) in
                    self.config_store.current.access.qq_reply_whitelist)

    def maintenance_allowed(self, scene_id):
        if scene_id.startswith('private:'):
            return True
        scene = self.scene(scene_id)
        return bool(scene and scene.enabled and scene.chat)

    def plugin_allowed(self, scene_id, plugin_id, role):
        scene = self.scene(scene_id)
        state = self.config_store.current.plugins.get(plugin_id)
        if scene_id.startswith('private:'):
            entry = self.config_store.catalog.entries.get(plugin_id)
            return bool(entry and entry.spec.private_tools
                        and state and state.enabled and state.config is not None)
        return bool(scene and scene.enabled and plugin_id in scene.plugins
                    and state and state.enabled and state.config is not None)

    def command_allowed(self, scene_id, command_id):
        scene = self.scene(scene_id)
        return bool(scene and command_id in scene.commands
                    and self.plugin_allowed(scene_id, 'asoul_calendar', 'command'))

    def announcement_allowed(self, scene_id, member):
        scene = self.scene(scene_id)
        return bool(scene and 'live_started' in scene.announcements
                    and member in scene.live_subscriptions
                    and self.plugin_allowed(scene_id, 'bilibili_live_sensor', 'announcement'))

    def monitored_members(self):
        names = {name for scene_id, scene in self.config_store.current.scenes.items()
                 if scene.enabled for name in scene.live_subscriptions}
        return [member for member in self.config_store.current.members if member.name in names]


def conversation_visible(event):
    return not event.metadata.get('conversation_excluded', False)

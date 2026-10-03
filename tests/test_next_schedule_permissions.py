"""Schedule permission decisions for explicit QQ identities and capability lists."""

import pytest

from len_bot.next.configuration.chat import ScheduleSettings
from len_bot.next.chat.schedule import check_cancellation, check_creation, identity_roles


BOT = "90001"
OWNER = "80001"
ADMIN = "80002"
WHITELISTED = "80003"
GROUP_MANAGER = "80004"
MEMBER = "80005"
OTHER = "80006"


def _settings(**changes) -> ScheduleSettings:
    return ScheduleSettings(owner=OWNER, admins=[ADMIN], whitelist=[WHITELISTED], **changes)


@pytest.mark.parametrize(
    ("requester", "group_role", "expected"),
    [
        (OWNER, None, {"member", "owner"}),
        (ADMIN, None, {"member", "admin"}),
        (WHITELISTED, None, {"member", "whitelist"}),
        (GROUP_MANAGER, "owner", {"member", "group_manager"}),
        (GROUP_MANAGER, "admin", {"member", "group_manager"}),
        (MEMBER, "member", {"member"}),
        (WHITELISTED, "admin", {"member", "whitelist", "group_manager"}),
    ],
)
def test_identity_roles_combine_configured_and_platform_roles(requester, group_role, expected):
    assert identity_roles(_settings(), requester, group_role) == expected


@pytest.mark.parametrize(
    ("requester", "group_role", "target", "allowed"),
    [
        (OWNER, None, "self", True),
        (ADMIN, None, ADMIN, True),
        (WHITELISTED, None, WHITELISTED, True),
        (GROUP_MANAGER, "admin", "self", True),
        (MEMBER, None, MEMBER, True),
        (OWNER, None, OTHER, True),
        (ADMIN, None, OTHER, True),
        (GROUP_MANAGER, "owner", OTHER, True),
        (GROUP_MANAGER, "admin", OTHER, True),
        (WHITELISTED, None, OTHER, False),
        (MEMBER, None, OTHER, False),
        (WHITELISTED, "admin", OTHER, True),
    ],
)
def test_default_schedule_creation_matrix(requester, group_role, target, allowed):
    kwargs = dict(requester=requester, target=target, bot_qq=BOT, group_role=group_role)
    if allowed:
        assert check_creation(_settings(), **kwargs) is None
    else:
        with pytest.raises(PermissionError, match="他人提醒"):
            check_creation(_settings(), **kwargs)


def test_creation_uses_configured_lists_without_privileged_bypass():
    settings = _settings(own=[], others=["whitelist"], manage=[])

    with pytest.raises(PermissionError, match="本人安排"):
        check_creation(settings, requester=OWNER, target="self", bot_qq=BOT, group_role=None)
    with pytest.raises(PermissionError, match="他人提醒"):
        check_creation(settings, requester=OWNER, target=OTHER, bot_qq=BOT, group_role=None)
    assert check_creation(settings, requester=WHITELISTED, target=OTHER,
                          bot_qq=BOT, group_role=None) is None


@pytest.mark.parametrize(
    ("settings", "requester", "target", "allowed", "error"),
    [
        (_settings(), None, "self", True, ""),
        (_settings(), None, OTHER, False, "实际请求人"),
        (_settings(autonomous=False), None, "self", False, "自主安排"),
        (_settings(enabled=False), OWNER, "self", False, "关闭安排"),
        (_settings(enabled=False), None, "self", False, "关闭安排"),
        (_settings(), BOT, "self", False, "requester=null"),
        (_settings(), BOT, OTHER, False, "requester=null"),
    ],
)
def test_creation_separates_autonomous_bot_from_human_permission(
    settings, requester, target, allowed, error
):
    kwargs = dict(requester=requester, target=target, bot_qq=BOT, group_role=None)
    if allowed:
        assert check_creation(settings, **kwargs) is None
    else:
        with pytest.raises(PermissionError, match=error):
            check_creation(settings, **kwargs)


@pytest.mark.parametrize(
    ("requester", "creator", "group_role", "allowed"),
    [
        (MEMBER, MEMBER, None, True),
        (WHITELISTED, WHITELISTED, None, True),
        (OWNER, MEMBER, None, True),
        (ADMIN, MEMBER, None, True),
        (GROUP_MANAGER, MEMBER, "admin", True),
        (GROUP_MANAGER, MEMBER, "owner", True),
        (WHITELISTED, MEMBER, None, False),
        (MEMBER, OTHER, None, False),
        (WHITELISTED, MEMBER, "admin", True),
        (None, None, None, True),
        (None, MEMBER, None, False),
        (OWNER, None, None, True),
        (BOT, None, None, False),
        (BOT, BOT, None, False),
    ],
)
def test_schedule_cancellation_matrix(requester, creator, group_role, allowed):
    kwargs = dict(requester=requester, creator=creator, bot_qq=BOT, group_role=group_role)
    if allowed:
        assert check_cancellation(_settings(), **kwargs) is None
    else:
        with pytest.raises(PermissionError):
            check_cancellation(_settings(), **kwargs)


def test_creator_can_cancel_after_creation_is_disabled_but_others_still_need_manage():
    settings = _settings(enabled=False, own=[], others=[], manage=[])

    assert check_cancellation(settings, requester=MEMBER, creator=MEMBER,
                              bot_qq=BOT, group_role=None) is None
    assert check_cancellation(settings, requester=None, creator=None,
                              bot_qq=BOT, group_role=None) is None
    with pytest.raises(PermissionError, match="管理能力"):
        check_cancellation(settings, requester=OWNER, creator=MEMBER,
                           bot_qq=BOT, group_role=None)


def test_root_owner_is_shared_with_schedule_capabilities():
    settings = ScheduleSettings(owner=None, own=['owner'], others=['owner'], manage=['owner'])
    check_creation(settings, requester=OWNER, target=OTHER, bot_qq=BOT, group_role=None, root_owner=OWNER)
    check_cancellation(settings, requester=OWNER, creator=OTHER, bot_qq=BOT, group_role=None, root_owner=OWNER)
    with pytest.raises(PermissionError):
        check_creation(settings, requester=MEMBER, target=OTHER, bot_qq=BOT, group_role=None, root_owner=OWNER)

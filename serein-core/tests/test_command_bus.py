import asyncio
from pathlib import Path

import pytest

from serein_core.command_bus import (
    CommandBus,
    CommandStatus,
    PermissionTier,
)

async def get_test_authenticated_session(bus: CommandBus, username: str = 'bus_admin', password: str = 'ValidSecurePass123!') -> str:
    from serein_core.auth import security_manager
    if username not in security_manager._users:
        security_manager.register_user(username, password, role='admin', age_confirmed=True)
    token = await bus.authenticate(username, password)
    assert token is not None, 'Authentication must succeed for valid registered credentials'
    return token



@pytest.mark.asyncio
async def test_read_only_command_is_available():
    bus = CommandBus()
    result = await bus.execute("get_system_status")
    assert result.success
    assert result.permission_tier is PermissionTier.READ_ONLY


@pytest.mark.asyncio
async def test_unknown_command_is_rejected():
    bus = CommandBus()
    result = await bus.execute("does_not_exist")
    assert result.status is CommandStatus.UNKNOWN


@pytest.mark.asyncio
async def test_reversible_command_requires_permission():
    bus = CommandBus()
    result = await bus.execute("launch_app", {"app": "python"})
    assert result.status is CommandStatus.DENIED


@pytest.mark.asyncio
async def test_reversible_command_can_be_dry_run():
    bus = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.REVERSIBLE},
        dry_run=True,
    )
    result = await bus.execute("launch_app", {"app": "python"})
    assert result.success
    assert result.result["dry_run"] is True


@pytest.mark.asyncio
async def test_destructive_command_requests_confirmation():
    bus = CommandBus(
        allowed_tiers={
            PermissionTier.READ_ONLY,
            PermissionTier.DESTRUCTIVE,
        }
    )
    sess = await get_test_authenticated_session(bus)
    result = await bus.execute("delete_file", {"path": "example.txt"}, session_id=sess)
    assert result.status is CommandStatus.CONFIRMATION_REQUIRED
    assert result.confirmation_token


@pytest.mark.asyncio
async def test_confirmation_can_be_declined():
    bus = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE}
    )
    sess = await get_test_authenticated_session(bus)
    requested = await bus.execute("shutdown", session_id=sess)
    assert requested.confirmation_token

    declined = await bus.confirm(requested.confirmation_token, False, session_id=sess)
    assert declined.status is CommandStatus.DENIED


@pytest.mark.asyncio
async def test_confirmation_token_is_single_use():
    bus = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE}
    )
    sess = await get_test_authenticated_session(bus)
    requested = await bus.execute("shutdown", session_id=sess)
    token = requested.confirmation_token

    declined = await bus.confirm(token, False, session_id=sess)
    assert declined.status is CommandStatus.DENIED

    again = await bus.confirm(token, False, session_id=sess)
    assert again.status is CommandStatus.FAILED


@pytest.mark.asyncio
async def test_custom_command_registration():
    bus = CommandBus()

    async def hello(params):
        return {"message": params["name"]}

    bus.register("hello", hello, PermissionTier.READ_ONLY)
    result = await bus.execute("hello", {"name": "Serein"})

    assert result.success
    assert result.result == {"message": "Serein"}


@pytest.mark.asyncio
async def test_custom_command_duplicate_is_rejected():
    bus = CommandBus()

    async def noop(_):
        return None

    bus.register("noop", noop)
    with pytest.raises(ValueError):
        bus.register("noop", noop)


def test_command_registry_contains_expected_capabilities():
    bus = CommandBus()
    names = {item["name"] for item in bus.list_commands()}
    assert {
        "get_system_status",
        "list_processes",
        "read_file",
        "launch_app",
        "close_process",
        "move_file",
        "delete_file",
        "shutdown",
        "reboot",
    } <= names

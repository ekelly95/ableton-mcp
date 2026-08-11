"""Shared fixtures: mock Live module + mock song/context for command tests."""

import pytest

from tests.mock_live import (
    MockControlSurface,
    MockSong,
    install_mock_live,
    uninstall_mock_live,
)


@pytest.fixture(scope="session", autouse=True)
def mock_live_module():
    """Commands do `import Live` lazily; give the whole test session a fake one."""
    install_mock_live()
    yield
    uninstall_mock_live()


@pytest.fixture()
def song() -> MockSong:
    return MockSong(track_count=2, scene_count=4, return_count=2)


@pytest.fixture()
def ctx(song):
    from control_surface.socket_server import CommandContext

    return CommandContext(MockControlSurface(song))


@pytest.fixture()
def registry():
    """The real global registry with all commands imported."""
    from control_surface.commands import REGISTRY

    return REGISTRY


def run_command(registry, ctx, name, /, **params):
    """Validate params exactly like the socket server would, then execute.

    Runs the handler through schedule_message so each command is exactly one
    scheduled task — matching the real one-marshal-per-request design. This is
    what makes the mock's deferred current_song_time (applied at task
    boundaries) behave in tests as it does against real Live.
    """
    schema = registry.get(name)
    assert schema is not None, f"command {name} not registered"
    validated = schema.validate_params(params)
    box = {}

    def task():
        box["result"] = schema.handler(ctx, **validated)

    ctx.control_surface.schedule_message(1, task)
    return box["result"]

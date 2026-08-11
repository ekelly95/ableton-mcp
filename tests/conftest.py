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


@pytest.fixture
def anyio_backend():
    return "asyncio"


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

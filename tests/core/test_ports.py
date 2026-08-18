import socket

import pytest

from pytest_webots._core import ports
from pytest_webots._core.errors import PortAllocationError


def listener(address: str = "127.0.0.1") -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((address, 0))
    sock.listen(1)
    return sock


def non_loopback_address() -> str | None:
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except socket.gaierror:
        return None
    return next((info[4][0] for info in infos if not info[4][0].startswith("127.")), None)


def test_available_false_while_listening() -> None:
    with listener() as sock:
        assert not ports.available(sock.getsockname()[1])


def test_available_false_for_non_loopback_listener() -> None:
    address = non_loopback_address()
    if address is None:
        pytest.skip("no non-loopback IPv4 address on this host")
    with listener(address) as sock:
        # Webots binds 0.0.0.0, so a listener on any interface is a conflict;
        # a 127.0.0.1-only probe would miss this one.
        assert not ports.available(sock.getsockname()[1])


def test_available_true_after_close() -> None:
    sock = listener()
    port = sock.getsockname()[1]
    sock.close()
    assert ports.available(port)


@pytest.mark.parametrize("port", [0, -1, ports.MAX_PORT + 1, 10**9])
def test_available_false_out_of_range(port: int) -> None:
    assert not ports.available(port)


def test_allocator_hands_out_increasing_ports() -> None:
    allocator = ports.PortAllocator(30000)
    first = allocator.acquire()
    second = allocator.acquire()
    assert 30000 <= first < second


def test_allocator_never_reissues_a_freed_port() -> None:
    allocator = ports.PortAllocator(30100)
    first = allocator.acquire()  # nothing binds it, so it is free again immediately
    assert allocator.acquire() > first


def test_allocator_skips_busy_ports() -> None:
    with listener() as sock:
        port = sock.getsockname()[1]
        assert ports.PortAllocator(port).acquire() > port


def test_allocator_raises_past_the_port_space() -> None:
    allocator = ports.PortAllocator(ports.MAX_PORT + 1)
    with pytest.raises(PortAllocationError, match="no free port"):
        allocator.acquire()

"""sdr_tuner.shared_bus - SharedBusHub / _AxisHandle. Tested with a hand-rolled fake bus (deterministic, no real
adapter timing to race) for the hub's own mechanics, plus one integration test attaching two real DriveClients to a
hub wrapping a single virtual CAN bus - the only place this hub is actually exercised in this project today (the
simulator's own multi-axis path never needs one: independent virtual-bus handles already fan out on their own - see
the module's own docstring), so this is where its fan-out and shared-send-lock behaviour has to be proven directly."""
import queue
import time

import can
import pytest

from sdr_tuner import protocol as P
from sdr_tuner import shared_bus
from sdr_tuner.client import DriveClient

WAIT = 2.0


def wait_until(fn, timeout=WAIT):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if fn():
            return True
        time.sleep(0.01)
    return fn()


class FakeBus:
    """A minimal, deterministic bus-like object for the hub's own tests: send() records what was sent, recv()
    returns from an in-order queue the test feeds directly (or blocks up to `timeout` like a real bus, then None)."""

    def __init__(self):
        self.sent = []
        self._incoming = queue.SimpleQueue()
        self._fail_next = None
        self.shutdown_called = False

    def send(self, msg, timeout=None):
        self.sent.append(msg)

    def push(self, msg):
        self._incoming.put(msg)

    def fail_next_recv(self, exc):
        self._fail_next = exc

    def recv(self, timeout=None):
        if self._fail_next is not None:
            exc, self._fail_next = self._fail_next, None
            raise exc
        try:
            return self._incoming.get(timeout=timeout)
        except queue.Empty:
            return None

    def shutdown(self):
        self.shutdown_called = True


def test_every_attached_handle_gets_a_copy_of_every_frame():
    bus = FakeBus()
    hub = shared_bus.SharedBusHub(bus)
    h1, h2 = hub.attach(), hub.attach()
    msg = can.Message(arbitration_id=0x181, data=b"\x01\x02", is_extended_id=False)
    bus.push(msg)
    assert h1.recv(timeout=WAIT) is msg
    assert h2.recv(timeout=WAIT) is msg


def test_send_from_any_handle_goes_straight_to_the_real_bus_under_one_shared_lock():
    bus = FakeBus()
    hub = shared_bus.SharedBusHub(bus)
    h1, h2 = hub.attach(), hub.attach()
    m1 = can.Message(arbitration_id=0x201, data=b"\x00", is_extended_id=False)
    m2 = can.Message(arbitration_id=0x202, data=b"\x00", is_extended_id=False)
    h1.send(m1)
    h2.send(m2)
    assert bus.sent == [m1, m2]


def test_the_real_bus_closes_only_once_every_handle_has_detached():
    bus = FakeBus()
    hub = shared_bus.SharedBusHub(bus)
    h1, h2 = hub.attach(), hub.attach()
    h1.shutdown()
    assert not bus.shutdown_called                          # h2 is still attached: the real link must stay open
    h2.shutdown()
    assert wait_until(lambda: bus.shutdown_called)


def test_on_closed_fires_once_the_real_bus_actually_closes_not_before():
    bus = FakeBus()
    closed = []
    hub = shared_bus.SharedBusHub(bus, on_closed=lambda: closed.append(True))
    h1, h2 = hub.attach(), hub.attach()
    h1.shutdown()
    assert closed == []
    h2.shutdown()
    assert wait_until(lambda: closed == [True])


def test_a_failed_real_recv_is_reported_to_every_attached_handle():
    bus = FakeBus()
    hub = shared_bus.SharedBusHub(bus)
    h1, h2 = hub.attach(), hub.attach()
    bus.fail_next_recv(RuntimeError("adapter unplugged"))
    with pytest.raises(RuntimeError, match="adapter unplugged"):
        h1.recv(timeout=WAIT)
    with pytest.raises(RuntimeError, match="adapter unplugged"):
        h2.recv(timeout=WAIT)


def test_two_real_driveclients_share_one_hub_on_one_virtual_bus_and_only_answer_their_own_node():
    """The one place this hub is actually exercised by this project today (see the module docstring): the
    simulator's own multi-axis path never needs one, since independent virtual-bus handles already fan out on
    their own. This proves the hub's fan-out and shared send lock work correctly against real DriveClients."""
    channel = "sdr-shared-bus-test"
    real_bus = can.Bus(interface="virtual", channel=channel)
    outsider = can.Bus(interface="virtual", channel=channel)      # stands in for "a drive answering" on the wire
    hub = shared_bus.SharedBusHub(real_bus)
    client_a = DriveClient(hub.attach(), node_id=3)
    client_b = DriveClient(hub.attach(), node_id=5)
    client_a.start()
    client_b.start()
    try:
        tpdo_cob = client_a.tpdos[0].cob
        outsider.send(can.Message(arbitration_id=P.can_id(tpdo_cob, 3), data=b"\x00" * 8, is_extended_id=False))
        assert wait_until(lambda: 3 in client_a.nodes_seen and 3 in client_b.nodes_seen)   # both hear it: one wire
        assert wait_until(lambda: client_a.last_tpdo is not None)                          # node 3's own client decodes it
        time.sleep(0.1)
        assert client_b.last_tpdo is None                                                  # node 5's client ignores it

        client_b._send(*P.read_request(5, 0x2000, 0x03))          # node 5 sending goes out over the real shared bus
        got = outsider.recv(timeout=WAIT)
        assert got is not None and P.split_can_id(got.arbitration_id) == (P.COB_REQUEST, 5)
    finally:
        client_a.close()
        client_b.close()
        outsider.shutdown()

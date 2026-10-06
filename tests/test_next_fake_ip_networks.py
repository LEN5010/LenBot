import pytest
from pydantic import ValidationError

from len_bot.next.work.egress_policy import NetworkSettings, blocked_resolved_reason


def test_fake_ip_range_admits_only_addresses_resolved_inside_it():
    networks = NetworkSettings(fake_ip_networks=["198.18.0.0/15"]).networks()
    # The address a proxy in fake-ip mode returned for www.google.com on 2026-10-05.
    assert blocked_resolved_reason("198.18.0.26", networks) is None
    assert blocked_resolved_reason("100.64.0.1", networks) is not None
    assert blocked_resolved_reason("192.168.1.10", networks) is not None
    assert blocked_resolved_reason("93.184.216.34", networks) is None
    assert blocked_resolved_reason("198.18.0.26", ()) == "私有或保留地址"


@pytest.mark.parametrize("value", [["10.0.0.0/8"], ["0.0.0.0/0"], ["8.8.8.0/24"], ["fd00::/8"], ["198.18.0.1/15"]])
def test_fake_ip_range_must_be_a_reserved_stand_in_range(value):
    with pytest.raises(ValidationError):
        NetworkSettings(fake_ip_networks=value)

import pytest
from pydantic import ValidationError

from len_bot.next.work.egress_policy import NetworkSettings, EgressBlocked, blocked_resolved_reason, parse_public_dns


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


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.5', '169.254.169.254', '198.18.0.26'])
def test_real_dns_response_cannot_turn_a_fake_address_into_private_egress(address):
    with pytest.raises(EgressBlocked, match=address):
        parse_public_dns({'Status': 0, 'Answer': [{'type': 1, 'data': address}]}, 1)


def test_public_dns_keeps_only_numeric_answers_for_the_requested_type():
    body = {'Status': 0, 'Answer': [{'type': 5, 'data': 'public.example.'}, {'type': 1, 'data': '93.184.216.34'}]}
    assert parse_public_dns(body, 1) == ['93.184.216.34']

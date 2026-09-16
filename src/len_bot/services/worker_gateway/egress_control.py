"""The one place an execution's egress control file is assembled.

The file a worker container reads is this module's output and nothing else:
the runner writes it, the worker library imports it, and both name the same
``egress_control`` file, so the in-container helper cannot quietly diverge from
the proxy's own rules.  The keys are the document ``EgressRules`` parses.
"""
from __future__ import annotations

from len_bot.services.worker_gateway.egress_policy import EgressRules

# Where the container reads the file: the control directory is mounted
# read-only at /lenbot-control, and this file is not an input, so it never
# collides with a manifest or an exported input of the same name.
CONTROL_RELATIVE_PATH = 'egress.json'
CONTROL_IN_CONTAINER = '/lenbot-control/' + CONTROL_RELATIVE_PATH


def control_document(rules: EgressRules, *, execution_id: str, proxy_url: str, bypass: str) -> dict:
    """The read-only egress file as the container sees it.

    ``bypass`` is the no-proxy list (the loopback address), not the egress
    rule; the rule is the proxy's, and the file says so where it can be read.
    """
    document = rules.document()
    document.update(execution_id=execution_id, proxy_url=proxy_url, no_proxy=bypass,
        note='本文件是本次执行的出口说明：规则由网关出口代理强制执行，'
             '容器内的检查只是为了在连接前给出同一句拒绝原因，本身不构成限制。')
    return document

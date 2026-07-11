"""Egress policy: docker arg construction and allowlist host matching.

The live proxy path needs a Docker daemon, so these tests pin the parts that
are deterministic without one: which network/env the task container gets per
policy, and the host-matching rules the proxy enforces.
"""

from pathlib import Path

import pytest

from pm_system.sandbox.egress_proxy import host_allowed, parse_allowlist
from pm_system.sandbox.runner import INTERNAL_NETWORK, DockerSandbox, EgressPolicy


def _args(sandbox):
    return sandbox._run_args(["python", "-m", "pytest"], Path("/tmp/w"), {}, "task-1")


def test_default_policy_is_no_network():
    args = _args(DockerSandbox())
    assert ["--network", "none"] == args[args.index("--network") : args.index("--network") + 2]
    assert not any(a.startswith("HTTP_PROXY=") for a in args)


def test_full_policy_uses_bridge():
    args = _args(DockerSandbox(egress=EgressPolicy.full()))
    assert "bridge" == args[args.index("--network") + 1]


def test_allowlist_policy_routes_through_proxy_on_internal_network():
    sandbox = DockerSandbox(egress=EgressPolicy.allowlist(["pypi.org", "*.pypi.org"]))
    args = _args(sandbox)
    assert INTERNAL_NETWORK == args[args.index("--network") + 1]
    proxy_envs = [a for a in args if a.startswith(("HTTP_PROXY=", "HTTPS_PROXY="))]
    assert len(proxy_envs) == 2
    assert all(sandbox._proxy_name() in v for v in proxy_envs)


def test_allowlist_requires_hosts():
    with pytest.raises(ValueError):
        EgressPolicy.allowlist([])


def test_proxy_name_is_stable_per_allowlist():
    a = DockerSandbox(egress=EgressPolicy.allowlist(["pypi.org"]))
    b = DockerSandbox(egress=EgressPolicy.allowlist(["pypi.org"]))
    c = DockerSandbox(egress=EgressPolicy.allowlist(["npmjs.org"]))
    assert a._proxy_name() == b._proxy_name()
    assert a._proxy_name() != c._proxy_name()


@pytest.mark.parametrize(
    "host,allowed,ok",
    [
        ("pypi.org", ["pypi.org"], True),
        ("files.pythonhosted.org", ["pypi.org"], False),
        ("sub.pypi.org", ["*.pypi.org"], True),
        ("pypi.org", ["*.pypi.org"], True),
        ("evilpypi.org", ["*.pypi.org"], False),
        ("PYPI.ORG", ["pypi.org"], True),
        ("pypi.org.evil.com", ["pypi.org"], False),
        ("anything.example", [], False),
    ],
)
def test_host_allowed(host, allowed, ok):
    assert host_allowed(host, allowed) is ok


def test_parse_allowlist():
    assert parse_allowlist(" pypi.org, *.NPMJS.org ,") == ["pypi.org", "*.npmjs.org"]

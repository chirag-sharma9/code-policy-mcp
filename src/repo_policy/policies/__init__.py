"""Importing this package registers the built-in policies."""

from repo_policy.policies import ci_tests, contact_email, package_usage  # noqa: F401
from repo_policy.policies.base import Policy
from repo_policy.policies.registry import PolicyRegistry, registry

__all__ = ["Policy", "PolicyRegistry", "registry"]

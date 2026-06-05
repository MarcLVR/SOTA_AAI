"""
Role-based permission policies with sliding-window rate limiting.

Roles
-----
viewer   — read-only chat (general agent only, no audit, no upload)
analyst  — chat + audit + upload; no code execution
admin    — unrestricted (default for local/UI use)

Configuration
-------------
ACTIVE_ROLE=analyst              # role applied to all UI sessions
PERMISSION_KEYS={"sk-abc":"analyst","sk-xyz":"admin"}  # api-key → role map
"""
from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass
from threading import Lock
from typing import Literal

# ── Policy definitions ────────────────────────────────────────────────────────

ALL = "*"

@dataclass(frozen=True)
class RolePolicy:
    allowed_agents: list[str] | str   # list or ALL
    allowed_tools:  list[str] | str   # list or ALL
    can_audit:  bool
    can_upload: bool
    rate_limit_requests: int   # max requests per window
    rate_limit_window:   int   # window in seconds


POLICIES: dict[str, RolePolicy] = {
    "viewer": RolePolicy(
        allowed_agents=["general"],
        allowed_tools=["retrieve_from_memory", "recall"],
        can_audit=False,
        can_upload=False,
        rate_limit_requests=10,
        rate_limit_window=60,
    ),
    "analyst": RolePolicy(
        allowed_agents=["general", "researcher", "auditor"],
        allowed_tools=[
            "retrieve_from_memory", "recall", "remember",
            "web_search", "read_file",
            "run_full_audit", "extract_document",
            "score_staleness", "check_standards", "check_governance",
        ],
        can_audit=True,
        can_upload=True,
        rate_limit_requests=30,
        rate_limit_window=60,
    ),
    "admin": RolePolicy(
        allowed_agents=ALL,
        allowed_tools=ALL,
        can_audit=True,
        can_upload=True,
        rate_limit_requests=120,
        rate_limit_window=60,
    ),
}

DEFAULT_ROLE = "admin"


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class PermissionResult:
    allowed: bool
    reason: str = ""
    role:   str = DEFAULT_ROLE


# ── Rate limiter (sliding window, thread-safe) ────────────────────────────────

_rate_lock = Lock()
_request_log: dict[str, deque[float]] = {}


def _check_rate_limit(identity: str, role: str, policy: RolePolicy) -> tuple[bool, str]:
    now = time.monotonic()
    with _rate_lock:
        log = _request_log.setdefault(identity, deque())
        cutoff = now - policy.rate_limit_window
        while log and log[0] < cutoff:
            log.popleft()
        if len(log) >= policy.rate_limit_requests:
            wait = int(policy.rate_limit_window - (now - log[0]))
            return False, (
                f"Rate limit reached for role '{role}' — "
                f"{policy.rate_limit_requests} requests/{policy.rate_limit_window}s. "
                f"Retry in ~{wait}s."
            )
        log.append(now)
    return True, ""


# ── Public API ────────────────────────────────────────────────────────────────

def resolve_role(api_key: str | None = None) -> str:
    """Resolve the active role from an API key or the ACTIVE_ROLE setting."""
    from config import settings
    if api_key:
        try:
            key_map: dict = json.loads(settings.permission_keys)
            if api_key in key_map:
                return key_map[api_key]
        except (json.JSONDecodeError, AttributeError):
            pass
    return getattr(settings, "active_role", DEFAULT_ROLE)


def get_policy(role: str) -> RolePolicy:
    return POLICIES.get(role, POLICIES[DEFAULT_ROLE])


def check_rate_limit(role: str, identity: str = "default") -> PermissionResult:
    policy = get_policy(role)
    ok, reason = _check_rate_limit(identity, role, policy)
    return PermissionResult(allowed=ok, reason=reason, role=role)


def check_agent(role: str, agent: str) -> PermissionResult:
    """Return whether *role* may route to *agent*."""
    policy = get_policy(role)
    if policy.allowed_agents == ALL or agent in policy.allowed_agents or agent == "FINISH":
        return PermissionResult(allowed=True, role=role)
    return PermissionResult(
        allowed=False,
        reason=f"Role '{role}' is not permitted to use the '{agent}' agent.",
        role=role,
    )


def check_action(role: str, action: Literal["audit", "upload", "code"], identity: str = "default") -> PermissionResult:
    """Check whether *role* may perform a named action and is within rate limits."""
    rl = check_rate_limit(role, identity)
    if not rl.allowed:
        return rl

    policy = get_policy(role)
    if action == "audit" and not policy.can_audit:
        return PermissionResult(allowed=False, reason=f"Role '{role}' does not have audit permission.", role=role)
    if action == "upload" and not policy.can_upload:
        return PermissionResult(allowed=False, reason=f"Role '{role}' does not have upload permission.", role=role)
    if action == "code" and (policy.allowed_agents != ALL and "coder" not in policy.allowed_agents):
        return PermissionResult(allowed=False, reason=f"Role '{role}' does not have code execution permission.", role=role)

    return PermissionResult(allowed=True, role=role)


def is_tool_allowed(role: str, tool_name: str) -> bool:
    policy = get_policy(role)
    return policy.allowed_tools == ALL or tool_name in policy.allowed_tools

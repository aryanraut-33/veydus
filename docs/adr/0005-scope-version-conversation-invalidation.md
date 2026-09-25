<!-- 
  Architecture Decision Record: Scope Version Conversation Invalidation
  What: Documents the architectural decision to enforce instant session invalidation
        via monotonic scope versions upon permission demotion or department transfer.
  Standards: Follows MADR template and HLD §9.3, §16.
-->

# ADR-0005: Scope Version Conversation Invalidation

| Field    | Value                                                              |
|----------|--------------------------------------------------------------------|
| Status   | Accepted                                                           |
| Date     | 2026-09-25                                                         |
| Decides  | Invalidate active conversations via monotonic scope_version checks |

## Context

When an administrator demotes a user's clearance level, reassigns them to a different department, or revokes their access grant, the user must immediately lose the ability to see or interact with restricted knowledge. 

However, multi-turn conversations contain prior turn history and rolling summaries that were generated using context the user was previously allowed to access. Allowing conversation continuation could allow users to extract confidential data from prior turns or rolling conversation context, creating a critical information leakage vector (HLD §9.3).

## Decision

Assign a monotonic integer `scope_version` on the `users` table, snapshot it as `conversations.scope_version` upon conversation creation, and invalidate any conversation whose version diverges from the user's active version.

## Rationale

1. **Immediate Revocation**: Any modification or revocation of an `access_grant` increments `users.scope_version` inside the same database transaction.
2. **Deterministic Chokepoint**: When a user submits `POST /conversations/{id}/messages`, the system compares `conversation.scope_version` with `user.scope_version`. If they mismatch, the conversation status is set to `'invalidated'` and the server returns `409 CONVERSATION_INVALIDATED`.
3. **No Expensive Context Rewriting**: Retroactively inspecting, redacting, or rewriting historical multi-turn messages and LLM summaries upon access changes is non-deterministic and computationally prohibitive. Complete conversation invalidation is mathematically sound and leaves no remnant context accessible.
4. **Local Scope Cache Eviction**: While user scopes are cached with a short 30-second TTL to reduce database query load on high-throughput chats, the `scope_version` check on conversation turns acts as a strict consistency barrier.

## Consequences

- Modifying an access grant automatically terminates all active conversation threads for that user.
- Frontend clients must handle `409 CONVERSATION_INVALIDATED` by disabling the input box and prompting the user to start a new conversation thread under their revised scope.
- Audit logs capture all grant alterations with their resulting `scope_version`.

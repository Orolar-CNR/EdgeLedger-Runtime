# Universal Command Contract Specification

## 1. Overview & Core Philosophy

The **Universal Command Contract** establishes a platform-neutral intent communication model across EdgeLedger Runtime components. All input sources—whether AI models, user interfaces, automation scripts, widgets, or voice assistants—must express requests using the standardized `IntentPayload` schema.

### Key Rules
1. **Platform Neutrality:** Request payloads must not contain platform-specific APIs or handles (such as Android `android.net.Uri` instances or Windows drive letters).
2. **AI Cannot Execute Directly:** AI agents generate `IntentPayload` structures marked with an `AI_PROPOSAL` origin. The system treats these as untrusted proposals subject to full policy evaluation and permission checks.
3. **Explicit Targets & Revisions:** Actions target resources identified by a `TargetDescriptor`, which includes revision markers (`mtime`, `size`, `etag` where applicable) to prevent race conditions during execution.

---

## 2. Intent Pipeline Architecture

```
[ AI / UI / Automation / Voice ]
               │
               ▼
       Universal Command
         (IntentPayload)
               │
               ▼
     Target Resolution & Validation
               │
               ▼
         Policy Engine
               │
               ▼
       Permission Manager
               │
               ▼
         Action Engine
               │
               ▼
     Platform Storage Adapter
               │
               ▼
      ActionResult & Audit Record
```

---

## 3. Data Contract Schemas

### 3.1 TargetDescriptor Schema
Identifies a resource in a platform-independent format.

```json
{
  "target_id": "string (UUID or persistent hash)",
  "scheme": "file | uri | ledger_ref",
  "path_identifier": "string (platform-neutral relative path or identifier)",
  "expected_size_bytes": 1048576,
  "expected_mtime_epoch_ms": 1700000000000,
  "expected_signature": "string (optional full or partial hash)"
}
```

### 3.2 IntentPayload Schema
Represents a requested operation submitted to the system.

```json
{
  "command_id": "string (UUID)",
  "timestamp_epoch_ms": 1700000000000,
  "source": {
    "channel": "AI | UI | AUTOMATION | SHORTCUT | VOICE",
    "agent_id": "string (e.g. 'insight_engine_v1' or 'user_ui')",
    "session_id": "string (optional)"
  },
  "action_type": "INDEX_SCAN | CALCULATE_HASH | RECOMMEND_DUPLICATES | DELETE_FILE | MOVE_FILE | QUARANTINE_FILE",
  "targets": [
    {
      "target_id": "target_123",
      "scheme": "file",
      "path_identifier": "documents/report.pdf",
      "expected_size_bytes": 2048576,
      "expected_mtime_epoch_ms": 1700000050000
    }
  ],
  "parameters": {
    "destination": "string (for MOVE_FILE)",
    "quarantine_reason": "string (for QUARANTINE_FILE)",
    "dry_run": false
  },
  "context": {
    "reasoning": "string (AI justification or user prompt)",
    "risk_ack_required": true
  }
}
```

### 3.3 ActionResult Schema
Returned by the `ActionEngine` after executing or rejecting an intent.

```json
{
  "command_id": "string (UUID)",
  "status": "SUCCESS | PARTIAL_SUCCESS | FAILED_POLICY_DENIED | FAILED_PERMISSION_DENIED | FAILED_STALE_REVISION | FAILED_IO_ERROR",
  "executed_at_epoch_ms": 1700000001000,
  "action_type": "DELETE_FILE",
  "results": [
    {
      "target_id": "target_123",
      "status": "SUCCESS",
      "bytes_affected": 2048576,
      "message": "File moved to trash bin successfully."
    }
  ],
  "error_details": null,
  "audit_record_id": "audit_98765"
}
```

---

## 4. Supported Action Types & Risk Categorization

| Action Type | Execution Nature | Default Risk Level | Required User Approval |
|---|---|---|---|
| `INDEX_SCAN` | Read-only | LOW | None (Background) |
| `CALCULATE_HASH` | Read-only | LOW | None (Background) |
| `RECOMMEND_DUPLICATES` | Read-only | LOW | None |
| `QUARANTINE_FILE` | Mutating (Non-destructive move) | MEDIUM | Required unless pre-authorized by policy |
| `MOVE_FILE` | Mutating | MEDIUM | Required unless pre-authorized |
| `DELETE_FILE` | Mutating (Destructive) | HIGH | Explicit user confirmation mandatory |

---

## 5. Platform Adapter Translation Rules

1. **Android Storage Adapter:**
   - Translates `TargetDescriptor.path_identifier` or `target_id` to an Android `android.net.Uri` (Content URI or Document URI) using Scoped Storage permissions.
   - Verifies target `mtime` and `size` via `DocumentFile` or `ContentResolver` query prior to performing any stream mutation.
   - If `mtime` or `size` differs from `TargetDescriptor.expected_*`, execution halts immediately with status `FAILED_STALE_REVISION`.

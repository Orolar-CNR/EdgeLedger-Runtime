# Policy Model Specification

## 1. Executive Summary & Core Rules

The EdgeLedger Runtime separates policy decisions from platform authorization:

* **Policy Engine:** Evaluates whether a requested command is safe, compliant with user preferences, and logically allowed. It is platform-agnostic and rule-driven.
* **Permission Manager:** Checks whether the operating system (e.g. Android OS permissions, Scoped Storage access grants) allows the application to execute the physical operation.

> **Fundamental Axiom:** Approval by the Policy Engine does NOT imply that the Permission Manager or Operating System will grant access. Neither component can override or substitute the other.

---

## 2. Policy Evaluation Flow

```
                  [ Universal Command (IntentPayload) ]
                                    │
                                    ▼
                         [ Target Resolution ]
                       (Verify mtime & size)
                                    │
                                    ▼
                          [ Policy Engine ]
                   (Risk Category & User Preferences)
                                    │
                       ┌────────────┴────────────┐
                 APPROVED                   REJECTED
                       │                         │
                       ▼                         ▼
             [ Permission Manager ]      [ ActionResult: FAILED_POLICY_DENIED ]
             (OS Permission Check)
                       │
         ┌─────────────┴─────────────┐
    PERMITTED                   DENIED
         │                           │
         ▼                           ▼
  [ Action Engine ]         [ ActionResult: FAILED_PERMISSION_DENIED ]
(Execute Operation)
```

---

## 3. Risk Classifications

Actions are categorized into three risk tiers:

### Tier 1 — Low Risk (Read-Only)
* **Actions:** `INDEX_SCAN`, `CALCULATE_HASH`, `RECOMMEND_DUPLICATES`
* **Policy Requirements:** Approved by default for files within configured storage scopes.
* **User Consent:** No explicit runtime prompt required.

### Tier 2 — Medium Risk (Non-Destructive Mutation)
* **Actions:** `QUARANTINE_FILE`, `MOVE_FILE`
* **Policy Requirements:** Requires destination validity check, verification that target file snapshot is current, and check against user quarantine settings.
* **User Consent:** Requires user approval or active rule configuration permitting automated relocation.

### Tier 3 — High Risk (Destructive Mutation)
* **Actions:** `DELETE_FILE`
* **Policy Requirements:** Strict target verification. Automated background deletion by AI proposals is prohibited without explicit user confirmation.
* **User Consent:** Mandatory user confirmation required. Must present target details (file name, size, preview, duplicate evidence) in UI before execution.

---

## 4. Audit Trail & Operation Logging

All executed or rejected intents must produce a immutable audit entry stored in the local audit log.

### Audit Entry Attributes
* `audit_id`: Unique record identifier (UUID).
* `command_id`: Reference to original `IntentPayload.command_id`.
* `timestamp_epoch_ms`: Execution timestamp.
* `action_type`: Specific command attempted.
* `source_channel`: Request source (`AI`, `UI`, `AUTOMATION`, `VOICE`).
* `target_count`: Number of files targeted.
* `bytes_affected`: Total size in bytes operated upon.
* `policy_decision`: `APPROVED` | `REJECTED`.
* `permission_decision`: `PERMITTED` | `DENIED`.
* `final_status`: `SUCCESS` | `FAILED_*`.
* `user_confirmation_obtained`: Boolean flag indicating explicit UI consent.

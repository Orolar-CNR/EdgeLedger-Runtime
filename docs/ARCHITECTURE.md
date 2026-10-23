# EdgeLedger Runtime — Architecture Specification

## 1. Executive Summary & Core System Definition

EdgeLedger Runtime is an Android-first, local-first data indexing, insight, and governed action runtime designed for resource-constrained edge environments. Its primary goal is to minimize I/O overhead, flash wear, battery consumption, and memory footprint when analyzing local storage by maintaining an incremental metadata index and executing staged cascading duplicate detection.

### Core Principles
1. **Contract Before Implementation:** Interfaces, payload structures, policy rules, and schemas are defined before concrete implementations are deployed.
2. **Evidence Before Claims:** System decisions, performance metrics, and duplicate candidates must be backed by explicit measurements (e.g., actual bytes read) rather than assumptions.
3. **AI Proposes; System Enforces:** AI, UI, voice, or automation components generate platform-neutral intent requests (`Universal Command`). AI output is untrusted and cannot execute storage actions directly.
4. **Separation of Policy and Permission:** Policy evaluation (business logic, risk assessment, user constraints) is decoupled from platform permission enforcement (Android OS access checks, Scoped Storage boundaries).
5. **Platform-Neutral Core:** Central contracts use platform-neutral resource identities (`TargetDescriptor`) that platform adapters resolve into platform-specific representations (e.g., Android Content URIs or File paths).
6. **Read-Only Analysis by Default:** Background scanning, signature computation, and insight generation are strictly read-only operations. File mutations require explicit intent dispatch, policy approval, permission validation, and audit logging.

---

## 2. High-Level Architecture Flow

The EdgeLedger system is divided into two distinct processing paths: the **Local Knowledge Path** (indexing, signature computation, insight generation) and the **Governed Action Path** (intent validation, policy assessment, permission enforcement, action dispatch).

```
AI / UI / Automation / Shortcuts / Widgets / Voice
                      │
                      ▼
            Universal Command (Intent Payload)
                      │
                      ▼
         Validation & Target Resolution
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
           Operating System / Storage
```

---

## 3. System Components & Responsibilities

| Component | Responsibility | Constraints & Boundaries |
|---|---|---|
| **Observation Adapters** | Receive file and media change signals from OS storage sources (e.g., Android `MediaStore` observers, `FileObserver`). | Signals may be delayed, dropped, or coalesced by the OS. Cannot be relied upon as the sole source of truth. |
| **Synchronization Layer** | Performs incremental index updates and triggers periodic or recovery reconciliation routines. | Must handle missed change events, deleted files, permission changes, and app restart state. |
| **Index Ledger** | Manages local SQLite storage for file metadata (`file_inventory`) and computed signatures (`file_signatures`). | Local index only; subject to snapshot consistency checks against actual filesystem state. |
| **Cascading Hash Pipeline** | Executes 3-stage duplicate candidate filtering: Stage 1 (Size), Stage 2 (Partial Hash), Stage 3 (Full Hash). | Computes signatures for specific file revision snapshots (`mtime`, `size`). Does not perform file mutations. |
| **Insight Engine** | Analyzes the `Index Ledger` to produce actionable, evidence-backed recommendations (e.g., duplicate candidates, large stale files). | Read-only analysis engine. Proposes recommendations; never executes mutations. |
| **Universal Command** | Normalizes input requests from AI, UI, or Voice into a structured `IntentPayload`. | Represents an intent request, not an authorization to execute. |
| **Policy Engine** | Evaluates requested actions against user configuration, risk levels, and safety rules. | Pure policy evaluation. Passing policy does not guarantee OS-level file access permission. |
| **Permission Manager** | Verifies active OS permissions (e.g., Android Scoped Storage, `READ_EXTERNAL_STORAGE`) and coordinates user consent UI. | Platform-specific enforcement layer. |
| **Action Engine** | Orchestrates validated, policy-approved, and permitted commands to appropriate command handlers. | Orchestration and execution recording. Writes structured outcomes to the Audit Log. |
| **Storage Adapter** | Translates `TargetDescriptor` identifiers into platform storage calls (e.g., `ContentResolver`, `File` stream). | Executes actual I/O. Does not contain policy or business logic. |

---

## 4. Phase Roadmap & Maturity Criteria

To ensure reliable, verifiable progress, development is structured into clear phases:

* **Phase 0 — Contract Definition (Implemented in `docs/`):** Define platform-neutral command contracts, database schemas, policy models, benchmark methodologies, and architectural boundaries.
* **Phase 1 — Python PoC & Mathematical Validation (Implemented in `prototypes/python/`):** Verify cascading hash logic, snapshot consistency, handling of unreadable/modified files, actual bytes read, and JSON metric reporting.
* **Phase 2 — Local Ledger & Persistence (Planned):** Implement Kotlin Room database schema, SQLite WAL tuning, migration triggers, and transaction recovery mechanisms.
* **Phase 3 — Android Storage & Resumable Worker (Planned):** Build Android `StorageAdapter` handling Scoped Storage / Content URIs, and WorkManager background worker with cancellation/resume logic.
* **Phase 4 — Policy, Permission & Governed Actions (Planned):** Implement `PolicyEngine`, `PermissionManager`, `ActionEngine`, and typed `ActionResult` handling.
* **Phase 5 — On-Device Benchmark & Real Hardware Validation (Planned):** Conduct physical device benchmarks (Snapdragon 8s Gen 4 / Android 16 target profile) measuring latency, bytes read, thermal response, and peak RAM.

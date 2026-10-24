# EdgeLedger Runtime — Android APK Prototype (Phase 2)

This directory contains the Phase 2 native Android prototype for **EdgeLedger Runtime**, implementing local-first file metadata indexing and 3-stage cascading duplicate candidate detection using Android's Storage Access Framework (SAF), Jetpack Room, and Jetpack Compose.

---

## 1. Features & Architecture

- **Storage Access Framework (SAF):** User selects a directory via `ACTION_OPEN_DOCUMENT_TREE`. Files and document trees are enumerated via `DocumentFile` and `ContentResolver` URIs.
- **Local SQLite Persistence:** Backed by Android Jetpack Room (`AppDatabase`) following `docs/INDEX_LEDGER_SCHEMA.md` (`file_inventory` and `file_signatures`).
- **Cascading Deduplication Pipeline:**
  1. **Stage 1 (Size Correlation):** Group by file size.
  2. **Stage 2 (Partial Hash):** Head-Tail 8 KiB window hashing via `SafScanner`.
  3. **Stage 3 (Full Hash):** Fixed-buffer streaming SHA-256 full hash over document URIs.
- **Strict Read-Only:** Operates in read-only mode without modifying, deleting, moving, or renaming user files.
- **User Interface:** Built with Kotlin & Jetpack Compose, showing live scanning progress, byte accounting, candidate duplicate groups, and estimated recoverable space breakdown.

---

## 2. How to Build the Android APK

### Prerequisites
- JDK 21
- Android SDK (API 34/36)
- Gradle 8.8 (included via `gradlew` wrapper)

### Build Command
From the project root:
```bash
cd android
./gradlew :app:assembleDebug
```

Upon successful compilation, the debug APK is generated at:
- `android/app/build/outputs/apk/debug/app-debug.apk`
- Top-level delivery location: `outputs/app-debug.apk`

---

## 3. Installation & Testing Instructions (For Android Tablet)

### Installing via ADB
1. Enable **Developer Options** and **USB Debugging** on your Android tablet.
2. Connect your tablet via USB.
3. Install the APK:
   ```bash
   adb install outputs/app-debug.apk
   ```

### Manual Testing Checklist
1. **Launch Application:** Open **EdgeLedger Runtime**.
2. **Select Directory:** Tap **Select Folder to Scan (SAF)** and choose a directory containing test files.
3. **Observe Progress:** Monitor file discovery, current stage, and byte-read metrics.
4. **Test Cancellation:** Tap **Cancel Scan** during scan execution to confirm safe cancellation.
5. **Review Report:** Inspect confirmed duplicate groups, retained copy size, and potential recoverable space.
6. **Verify Read-Only Safety:** Confirm no user files were modified or deleted.

---

## 4. Automated Test Commands

### Python Reference PoC Test Suite
```bash
python3 -m unittest discover -s prototypes/python/tests
```

### Android Unit Tests
```bash
cd android
./gradlew test
```

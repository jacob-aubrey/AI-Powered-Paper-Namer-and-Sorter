# App review and improvement work list

Reviewed startup/packaging, Watch & Launch, settings, GUI queues and confirmations, extraction, DOI/AI lookup, filename validation, and existing tests.

## Completed

- [x] Remove the self-extracting startup delay; distribute the executable with its required `_internal` folder.
- [x] Delay PDF, Word, and Gemini imports until a document needs them.
- [x] Open immediately on supported watcher events; keep readiness checks in the GUI processing worker.
- [x] Replace PowerShell instance detection with a native mutex, prevent duplicate launches, scan on helper startup, and ignore moves out.
- [x] Keep helpers independent of the GUI lifetime and refresh enabled registration after upgrading the app location.
- [x] Keep processing workers alive after an unexpected document failure.
- [x] Buffer background logging so worker threads never block on Tk.
- [x] Require explicit confirmation for duplicates and final moves; dismissal cancels.
- [x] Bound DOI retries and Gemini requests, preserving local fallback.
- [x] Reject generic short-title matches against longer DOI titles and reject Windows device-name prefixes before extra extensions.
- [x] Resolve historical filenames with brackets literally and open library folders through Windows.
- [x] Describe **Watch folder** and **Library folder** as roles, accepting arbitrary names and category subfolders.
- [x] Correct repeated DPI scaling during dialog centering and use the owner's monitor work area.
- [x] Add regression coverage and an offline packaged-app self-test.

## 1.3.1 validation

Citation names, SI local extraction and simulated AI fallback, privacy controls, versioned window activation, and independent background controls have regression coverage. Source diagnostics construct the UI and exercise extraction and bundled assets without contacting external services. Watcher logs expose background failures.

## 1.3.0 measurements on this PC

| Check | Previous | Updated |
| --- | --- | --- |
| App imports, median of 3 source-process runs | 1.723 s | 0.469 s |
| Packaged startup probe, median of 3 runs | 11.158 s | 0.220 s |
| New file to watcher launch callback | not measured | 1.05 ms |
| Completed download rename to launch callback | not measured | 0.83 ms |

The packaged startup probe runs watcher mode with isolated, unconfigured settings and exits; it measures executable/runtime startup overhead, not a full user workflow. An offline packaged UI check constructed the main window in 0.788 s after entering Python. These are local observations, not guarantees on every PC. Files still need to settle before reading, and metadata analysis can take longer than opening the window.

Validation: 86 automated tests; actual Windows filesystem creation, modification, download-renaming and move-out events; offline packaged UI, fonts/icons, TkDnD, messagebox, PDF/DOCX extraction, AI-client initialization, and log-link checks. No live metadata request was needed. A separate PC/account has not been tested.

The 12-second DOI budget is a shared retry deadline, and the 20-second Gemini setting is an SDK request timeout with one attempt. OS/network behavior can exceed those intervals; neither is a hard real-time wall-clock guarantee.

## Suggested next upgrades

- [ ] Move large file copies/moves and recursive library searches off the GUI thread, with progress and cancellation. Those operations can still pause the window on large files or slow/network folders.
- [x] Show background helper status with Start/Stop/Restart and retry temporary folder/notification failures (1.3.1).
- [ ] Add a library filename index and an undo-last-move action, with collision checks.
- [ ] Offer optional OCR for scanned PDFs, clearly distinguishing extracted text from verified metadata.

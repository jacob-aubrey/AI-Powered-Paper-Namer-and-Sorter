# AI Paper Sorter

AI Paper Sorter is a Windows Python desktop app for sorting and renaming research paper PDFs.

The app reads the first pages of each PDF, asks Gemini to extract paper metadata, proposes a filename, lets the user approve or edit it, then moves the paper into a selected folder under the configured sorted-paper library.

AI naming is optional. If no Gemini API key is configured, the app can still use Basic naming from PDF metadata, first-page text, and the original filename.

## Source Files

- `src/main.py` - small entry point that starts the app.
- `src/app.py` - main GUI and sorting workflow.
- `src/core_logic.py` - PDF extraction, Gemini parsing, filename validation, and path helpers.
- `src/settings.py` - per-PC settings loading and saving.
- `src/watch_and_launch.py` - optional low-power companion watcher that launches the sorter when a PDF appears in the To Sort folder.
- `assets/` - app icons.

## Configuration

The app has a Settings button in the top-right corner. On first run, or when folders are missing, it prompts the user to choose:

- `To Sort folder`
- `Sorted papers root folder`
- `Naming mode`
- Optional `Gemini API key`

Those settings are saved per PC. A `config.example.json` file is included for reference.

Naming modes:

- `Automatic` - use AI when a Gemini key exists, otherwise use Basic naming.
- `AI` - prefer Gemini naming; falls back to Basic if no key is available or AI fails.
- `Basic` - never uses Gemini.

The app can read a Gemini key from Settings or from the `GEMINI_API_KEY` environment variable.

## Optional Watch And Launch

`Watch and Launch` is a separate lightweight helper. Leave it running if you want Windows to watch the configured To Sort folder while the main sorter app is closed.

When a PDF is created, moved into, or modified in the To Sort folder, the helper waits until the file looks fully copied, then opens AI Paper Sorter if it is not already running.

This feature is off by default. Turn it on in Settings with `Start Watch and Launch at Windows login/unlock`.

When enabled, the app creates a per-user Windows Scheduled Task that starts the helper at login and when the workstation is unlocked.

If Windows blocks Scheduled Task creation, the app falls back to a Startup folder command so the helper still starts at login.

# AI Paper Sorter

AI Paper Sorter is a Windows Python desktop app for sorting and renaming research paper PDFs.

The app reads the first pages of each PDF, asks Gemini to extract paper metadata, proposes a filename, lets the user approve or edit it, then moves the paper into a selected folder under the configured sorted-paper library.

## Source Files

- `src/main.py` - small entry point that starts the app.
- `src/app.py` - main GUI and sorting workflow.
- `src/core_logic.py` - PDF extraction, Gemini parsing, filename validation, and path helpers.
- `src/settings.py` - per-PC settings loading and saving.
- `src/watch_and_launch.py` - optional companion watcher/launcher.
- `assets/` - app icons.

## Configuration

The app has a Settings button in the top-right corner. On first run, or when folders are missing, it prompts the user to choose:

- `To Sort folder`
- `Sorted papers root folder`

Those settings are saved per PC. A `config.example.json` file is included for reference.

The app also requires a `GEMINI_API_KEY` environment variable.

# AI Paper Sorter

AI Paper Sorter is a Windows Python desktop app for sorting and renaming research paper PDFs.

The app reads the first pages of each PDF, asks Gemini to extract paper metadata, proposes a filename, lets the user approve or edit it, then moves the paper into a selected folder under the configured sorted-paper library.

## Source Files

- `Python Scripts and Miscelaneous Files/ai_paper_sorter.py` - main GUI app.
- `Python Scripts and Miscelaneous Files/core_logic.py` - PDF extraction, Gemini parsing, filename validation, and path helpers.
- `Python Scripts and Miscelaneous Files/gui_components.py` - reusable GUI components.
- `Python Scripts and Miscelaneous Files/watch_and_launch.py` - optional companion watcher/launcher.

## Configuration

Copy `Python Scripts and Miscelaneous Files/config.example.json` to `config.json` next to the app or executable, then edit the paths.

The app also requires a `GEMINI_API_KEY` environment variable.

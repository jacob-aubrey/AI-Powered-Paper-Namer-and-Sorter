# AI Paper Sorter

AI Paper Sorter is a Windows desktop app for reviewing, naming, and filing research material. It supports text-based PDFs and modern Microsoft Word documents (`.docx`). It extracts a small amount of metadata, proposes a safe filename, and always lets you approve or edit the result before it moves the file.

It is designed to be helpful, not fully automatic: unclear, non-journal, scanned, or unusual material is flagged for human review instead of being silently treated as a journal article.

## What you need

- Windows 10 or 11.
- A folder for incoming documents and a different folder for your organized library.
- Nothing else for **Basic** naming.
- Your own Google Gemini API key only if you want AI-assisted naming.

Supported inputs are `.pdf` and `.docx`. The app does not yet read old binary Word `.doc` files; open those in Word and save them as `.docx` first. Image-only/scanned PDFs need OCR before the app can extract useful metadata.

## Quick start (for most people)

1. Download the latest Windows release from the [Releases page](https://github.com/jdaub00/AI-Powered-Paper-Namer-and-Sorter/releases).
2. Extract the whole ZIP to a permanent location, such as `Documents\AI Paper Sorter`. Do not run it from inside the ZIP.
3. If the release contains an `_internal` folder, keep it next to the executable. Do not distribute or copy only the `.exe`.
4. Open **AI Paper Sorter**. On first use, open **Settings** and choose:
   - **To Sort folder** — a staging folder for incoming PDFs and Word documents.
   - **Sorted papers root** — the top-level folder that will hold your organized library.
5. Keep those folders separate. The app will refuse folders that are the same or nested inside each other, which prevents accidental watch loops.
6. Leave **Naming mode** on **Automatic** for local Basic naming until you add your own Gemini key, or choose **Basic** if you never want cloud AI assistance.
7. Drag documents onto the app or click **browse**. Review the suggested filename, choose a destination folder, and click **Confirm**.

When you add a document through the app, it first copies the original into the To Sort folder; your original stays where it was. If you manually place a file in the To Sort folder, that copy is the one that will be moved after you approve it.

Windows may show a SmartScreen message because personal builds are not code-signed. Only choose **More info → Run anyway** after you have confirmed the file came from a person or release you trust.

## How naming works

The app has three modes:

| Mode | What it does |
| --- | --- |
| **Automatic** | Uses Gemini for PDFs when a key is available; otherwise uses Basic naming. Word documents remain Basic unless you separately opt in below. |
| **AI** | Prefers Gemini for PDFs and falls back to Basic if the key, network, or AI response fails. Word documents still need their separate opt-in. |
| **Basic** | Uses local document metadata, readable text, and the existing filename. It never sends document text to Gemini. |

For journal articles, the proposal usually follows the familiar author / venue / year pattern. For a report, thesis, book chapter, guideline, preprint, letter, or uncertain document, the app uses a safer title-based proposal and marks it for review. You can edit every filename before continuing.

The file type is always preserved: a `.docx` stays a `.docx`, and a `.pdf` stays a `.pdf`.

If a file with the final name already exists in the chosen destination, a sorted copy receives `-1`, `-2`, and so on instead of overwriting anything. Rename-in-place skips a filename collision rather than replacing the existing file.

### Choose a filename style

In **Settings**, choose a **Filename style** independently from the AI/Basic mode. **Smart (recommended)** keeps the app’s conservative behavior: journal-like documents use creator / venue / year when those fields are reliable; other material uses title / year / type.

The other presets are useful when your library needs a consistent citation shape:

| Style | Example |
| --- | --- |
| Compact journal citation | `Doe_et_al_J_Example_Res_2024.pdf` |
| Detailed journal citation | `Doe_et_al_Journal_of_Example_Research_12_3_2024.pdf` |
| Author – year – title | `Doe_et_al_2024_A_Practical_Example_Study.pdf` |
| Title – year – type | `A_Practical_Example_Study_2024_Journal_Article.pdf` |
| Custom template | Your selected supported fields in your own order. |

For **Custom template**, Settings shows the allowed tokens and a live example. Templates cannot run code, include paths, or change `.pdf`/`.docx` extensions. A journal abbreviation is used only when the document supports it; the app does not invent abbreviations. If a journal-only style lacks the required journal fields, it safely falls back to Smart naming and still asks you to review the result.

## Set up your own Gemini API key (optional)

Each person should use **their own** key. Never share your key, put it in a screenshot, commit it to GitHub, or copy someone else’s configured app data.

1. In the app, open **Settings** and click **Get Key**. This opens [Google AI Studio](https://aistudio.google.com/app/apikey).
2. Sign in with your own Google account, create a new Gemini API key, and copy it.
3. Paste it into **Gemini API key**.
4. Set **Naming mode** to **Automatic** or **AI**, then click **Save**.
5. Try one non-sensitive document first. If AI naming fails, the app automatically uses Basic naming instead.

The settings copy of a key is encrypted with Windows Data Protection and is usable only by the Windows account that saved it. Older plain-text settings are migrated when saved again. Do not share `%APPDATA%\AI Paper Sorter\config.json` or a previously configured app folder anyway.

Advanced users can supply a key as a user environment variable instead:

```powershell
setx GEMINI_API_KEY "PASTE_YOUR_KEY_HERE"
```

Close and reopen the app after running that command. A key entered in Settings takes precedence over `GEMINI_API_KEY`.

### Privacy, cost, and accuracy

- In AI mode, the app sends an extracted text excerpt to Gemini so it can identify metadata. Basic mode stays local.
- Do not use AI mode for confidential, patient, personnel, legal, or otherwise sensitive documents unless you understand and accept Google’s current terms and data handling.
- Gemini’s free tier may use submitted content to improve Google products; paid-tier handling can differ. Check [Google’s current pricing and data-use information](https://ai.google.dev/gemini-api/docs/pricing) before using it.
- Your API use and any charges belong to the Google account that owns the key. Check the [AI Studio API-key page](https://ai.google.dev/gemini-api/docs/api-key) for quota, billing, and key-management details.
- AI can be wrong. Treat every suggestion as a draft and verify it before moving a document.

## Watch folders and Watch & Launch

There are two related features:

- **In-app watching** runs while AI Paper Sorter is open. It notices supported files placed directly in the To Sort folder and queues them for review.
- **Watch & Launch** is the optional background helper controlled by **Settings → Enable Watch & Launch at Windows login/unlock**. When enabled, Windows starts a lightweight watcher at sign-in and unlock; it opens the app when a supported file appears while the main window is closed.

Watch & Launch is **off by default**. Turn the checkbox off and save to remove its scheduled task/startup fallback and stop this app’s helper. The checkbox does not control normal in-app watching.

The watched folder is the **To Sort folder** in Settings. You can change it at any time; the app restarts its in-app watcher after a successful save. Watching is direct-folder only, not recursive through subfolders. Temporary Word files whose name starts with `~$` are ignored.

## Daily use

### Sort incoming material

1. Add a PDF or `.docx` file by drag-and-drop, **browse**, or by placing it in the To Sort folder.
2. Wait for the file to finish copying. The app coalesces Windows file-system events, so one copied document becomes one review action.
3. Check the document title/type and any review warning.
4. Edit or accept the proposed filename.
5. Pick a destination under your Sorted papers root and confirm the move.

### Rename files without moving them

Click **Name Papers**, then choose a folder or individual supported files. This changes names in place, one review at a time. It does not move files to the library.

### Find the log

Click **Log** in the app, or open `paper_sorter_log.txt` in the Sorted papers root. Moved-file entries include clickable links to the destination folder and file. The on-screen log is read-only; **Clear Display** removes only what is currently shown and never deletes the log file. The log is useful because there is no automatic undo feature yet.

## Troubleshooting

| Problem | Try this |
| --- | --- |
| The app will not start | Extract the complete release ZIP. If it includes `_internal`, it must remain beside the executable. |
| Windows SmartScreen blocks it | Verify the source first. Then use **More info → Run anyway** only if you trust the download. |
| The app asks for folders | Open **Settings**, choose both separate folders, and click **Save**. |
| A file is not detected | Confirm it is a `.pdf` or `.docx`, is placed directly in the To Sort folder, and is not a Word `~$` temporary file. Use **Refresh** to scan the folder again. |
| A scan has a bad title or no metadata | The PDF may be image-only. Run OCR or enter a name manually. |
| AI naming is unavailable | Confirm your own key, internet access, Google AI Studio quota/billing, and try Basic mode. The file can still be sorted manually. |
| Watch & Launch does not wake the app | Open Settings, turn the checkbox off and save, then turn it back on and save. Keep the installed app in a permanent location. |
| A `.doc` file is rejected | Save it as a modern `.docx` file first. |
| A suggestion for a report/thesis looks odd | This is expected for non-journal material. Review and edit the proposal; the app deliberately avoids inventing journal metadata. |

## Build from source

This path is for people who want to develop or package the app themselves.

```powershell
git clone https://github.com/jdaub00/AI-Powered-Paper-Namer-and-Sorter.git
cd AI-Powered-Paper-Namer-and-Sorter
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python src\main.py
```

Run the tests with:

```powershell
python -m unittest discover -s tests -v
```

Build a Windows package with:

```powershell
pyinstaller --noconfirm "AI Paper Sorter.spec"
```

Share only the fresh, versioned build you produced—not an old mixture of files from `dist`. Before publishing a release, scan it for secrets, test it on a different Windows account/PC, create a checksum, and write clear release notes.

## Project status and contributing

The source repository is [AI-Powered-Paper-Namer-and-Sorter](https://github.com/jdaub00/AI-Powered-Paper-Namer-and-Sorter). It is a real Git repository with `main` tracking `origin/main`.

Before making it broadly public, choose a license and add it to the repository; without one, other people do not automatically have permission to redistribute or modify the code. Please report reproducible issues with the Windows version, input type, naming mode, and a redacted log excerpt—never an API key or confidential document.

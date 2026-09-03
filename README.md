# AI Paper Sorter

AI Paper Sorter is a Windows desktop app for reviewing, naming, and filing research material. It supports text-based PDFs, modern Microsoft Word documents (`.docx`), and PowerPoint presentations (`.pptx` plus cautious legacy `.ppt` support). It gathers useful metadata, proposes a safe filename, and always lets you approve or edit the result before it moves the file.

It is designed to be helpful, not fully automatic: unclear, non-journal, scanned, or unusual material is flagged for human review instead of being silently treated as a journal article.

## What you need

- Windows 10 or 11.
- A folder for incoming documents and a different folder for your organized library.
- Nothing else for local-only naming.
- Your own Google Gemini API key only if you want Gemini to help when the app cannot identify a document from its local information and DOI record.

Supported inputs are `.pdf`, `.docx`, `.pptx`, and legacy `.ppt` files. The app can read useful text and metadata from modern `.pptx` presentations. It can safely move and rename old `.ppt` presentations too, but treats them more cautiously because the older format is harder to read reliably. The app does not yet read old binary Word `.doc` files; open those in Word and save them as `.docx` first. Image-only/scanned PDFs need OCR before the app can extract useful metadata.

## Quick start (for most people)

1. Download the latest Windows release from the [Releases page](https://github.com/jacob-aubrey/AI-Powered-Paper-Namer-and-Sorter/releases).
2. Extract the whole ZIP to a permanent location, such as `Documents\AI Paper Sorter`. Do not run it from inside the ZIP.
3. If the release contains an `_internal` folder, keep it next to the executable. Do not distribute or copy only the `.exe`.
4. Open **AI Paper Sorter**. On first use, open **Settings** and choose:
   - **To Sort folder** — a staging folder for incoming PDFs, Word documents, and PowerPoint presentations.
   - **Sorted papers root** — the top-level folder that will hold your organized library.
5. Keep those folders separate. The app will refuse folders that are the same or nested inside each other, which prevents accidental watch loops.
6. Leave **Naming mode** on **Smart metadata lookup (recommended)**. It starts with information already inside the document, checks an exact DOI when one is found, and uses Gemini only as a backup when you have added your own key. Choose **Local-only privacy mode** if you never want online lookup or Gemini assistance.
7. Drag documents onto the app or click **browse**. Review the suggested filename, choose a destination folder, and click **Confirm**.

When you add a document through the app, it first copies the original into the To Sort folder; your original stays where it was. If you manually place a file in the To Sort folder, that copy is the one that will be moved after you approve it.

Windows may show a SmartScreen message because personal builds are not code-signed. Only choose **More info → Run anyway** after you have confirmed the file came from a person or release you trust.

## How naming works

The recommended **Smart metadata lookup** method works in this careful order:

1. It first reads information already inside the file, such as the title, author, DOI, and document properties.
2. If it finds an exact DOI, it can look up the DOI’s public citation record and checks that it agrees with the file.
3. If those exact sources are incomplete or unavailable, it may use Gemini as a backup—but only when you have supplied your own key and the file type is allowed below.
4. If the app still cannot identify the file safely, it gives you a conservative, editable suggestion instead of making up a journal citation.

| Naming mode | What it does |
| --- | --- |
| **Smart metadata lookup (recommended)** | Uses local information first. Its DOI/citation lookup is on by default and sends only an exact DOI to a scholarly metadata service, not the paper or slides. Gemini is a backup, not the first choice. |
| **Local-only privacy mode** | Uses only information already on your computer: the file’s metadata, readable text, and existing filename. It automatically skips both DOI web lookup and Gemini, even if their other Settings checkboxes remain selected. |

The online DOI/citation lookup within Smart metadata lookup can be turned off in Settings. That is useful if you want its local checks but do not want it to contact a metadata service. If the app does use Gemini, the separate Word and PowerPoint permissions still apply.

For journal articles, the proposal usually follows the familiar author / venue / year pattern. For a report, thesis, book chapter, guideline, preprint, letter, presentation, or other uncertain document, the app uses a safer title-based proposal. You can edit every filename before continuing, and the app calls attention to genuinely missing or conflicting information.

Instead of showing a mysterious percentage, the review window explains the source of its suggestion in plain English—for example, that it came from a verified DOI record, that the DOI record matched the document, or that Gemini was used as a backup. It asks for attention only when something is missing, conflicts, or needs your judgment.

The file type is always preserved: a `.pdf` stays a `.pdf`, a `.docx` stays a `.docx`, and a PowerPoint file keeps its own `.pptx` or `.ppt` ending.

If a file with the final name already exists in the chosen destination, a sorted copy receives `-1`, `-2`, and so on instead of overwriting anything. Rename-in-place skips a filename collision rather than replacing the existing file.

### Choose a filename style

In **Settings**, choose a **Filename style** independently from the naming method. The **Smart (recommended)** filename style keeps the app’s conservative behavior: journal-like documents use creator / venue / year when those fields are reliable; other material uses title / year / type.

The other presets are useful when your library needs a consistent citation shape:

| Style | Example |
| --- | --- |
| Compact journal citation | `Doe_et_al_J_Example_Res_2024.pdf` |
| Detailed journal citation | `Doe_et_al_Journal_of_Example_Research_12_3_2024.pdf` |
| Author – year – title | `Doe_et_al_2024_A_Practical_Example_Study.pdf` |
| Title – year – type | `A_Practical_Example_Study_2024_Journal_Article.pdf` |
| Custom template | Your selected supported fields in your own order. |

For **Custom template**, Settings shows the allowed tokens and a live example. Templates cannot run code, include paths, or change `.pdf`, `.docx`, `.pptx`, or `.ppt` extensions. A journal abbreviation is used only when the document supports it; the app does not invent abbreviations. If a journal-only style lacks the required journal fields, it safely falls back to the Smart filename style and still asks you to review the result.

### Supporting information

When the app can **confidently** tell that a file is supporting or supplementary information for a paper, it adds `_SI` just before the file extension. For example:

`Pawelec_Communications_Materials_2026_SI.pdf`

It looks for strong signs, such as “Supporting Information” on the file itself or a confirmed relationship to a parent article. It does not add `_SI` just because the main paper happens to mention supplements. If it is unsure, it explains that the file may be supplementary material and leaves the proposed name fully editable.

## Set up your own Gemini API key (optional AI backup)

Each person should use **their own** key. Never share your key, put it in a screenshot, commit it to GitHub, or copy someone else’s configured app data.

1. In the app, open **Settings** and click **Get Key**. This opens [Google AI Studio](https://aistudio.google.com/app/apikey).
2. Sign in with your own Google account, create a new Gemini API key, and copy it.
3. Paste it into **Gemini API key**.
4. Set **Naming mode** to **Smart metadata lookup**, then click **Save**.
5. Try one non-sensitive document first. If Gemini cannot help, the app keeps the suggestion conservative and you can edit it before anything moves.

Gemini is optional. Smart metadata lookup can still use information inside a file and an exact DOI record without a Gemini key. **Local-only privacy mode** does not use either service.

In Settings, Word and PowerPoint have their own clear permissions:

- **Allow AI analysis of Word documents (.docx)**
- **Allow AI analysis of PowerPoint presentations (.pptx)**

Those boxes start off. Even in Smart metadata lookup mode, the app will not send extracted Word or `.pptx` slide text to Gemini unless you deliberately turn on the matching box. Older `.ppt` files are handled cautiously and may need more of your review.

The settings copy of a key is encrypted with Windows Data Protection and is usable only by the Windows account that saved it. Older plain-text settings are migrated when saved again. Do not share `%APPDATA%\AI Paper Sorter\config.json` or a previously configured app folder anyway.

Advanced users can supply a key as a user environment variable instead:

```powershell
setx GEMINI_API_KEY "PASTE_YOUR_KEY_HERE"
```

Close and reopen the app after running that command. A key entered in Settings takes precedence over `GEMINI_API_KEY`.

### Privacy, cost, and accuracy

- **Local-only privacy mode** stays on your computer. It does not contact a DOI/citation website and does not send text to Gemini.
- In **Smart metadata lookup** mode, a DOI lookup sends only the exact DOI found in the document to a scholarly metadata service. It does **not** upload the document or its text just to perform that lookup.
- If Smart metadata lookup needs Gemini as a backup, it sends an extracted text excerpt to Gemini. Word and `.pptx` text need their separate opt-ins above; PDF text follows Smart metadata lookup’s normal backup process when a Gemini key is available.
- Do not use Gemini for confidential, patient, personnel, legal, or otherwise sensitive documents unless you understand and accept Google’s current terms and data handling.
- Gemini’s free tier may use submitted content to improve Google products; paid-tier handling can differ. Check [Google’s current pricing and data-use information](https://ai.google.dev/gemini-api/docs/pricing) before using it.
- Your API use and any charges belong to the Google account that owns the key. Check the [AI Studio API-key page](https://ai.google.dev/gemini-api/docs/api-key) for quota, billing, and key-management details.
- A DOI record or AI suggestion can still be wrong or incomplete. Treat every proposed name as a draft and verify it before moving a document.

## Watch folders and Watch & Launch

There are two related features:

- **In-app watching** runs while AI Paper Sorter is open. It notices supported files placed directly in the To Sort folder and queues them for review.
- **Watch & Launch** is the optional background helper controlled by **Settings → Enable Watch & Launch at Windows login/unlock**. When enabled, Windows starts a lightweight watcher at sign-in and unlock; it opens the app when a supported file appears while the main window is closed.

Watch & Launch is **off by default**. Turn the checkbox off and save to remove its scheduled task/startup fallback and stop this app’s helper. The checkbox does not control normal in-app watching.

The watched folder is the **To Sort folder** in Settings. You can change it at any time; the app restarts its in-app watcher after a successful save. Watching is direct-folder only, not recursive through subfolders. Temporary Office files whose name starts with `~$` are ignored.

## Daily use

### Sort incoming material

1. Add a PDF, `.docx`, `.pptx`, or `.ppt` file by drag-and-drop, **browse**, or by placing it in the To Sort folder.
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
| A file is not detected | Confirm it is a `.pdf`, `.docx`, `.pptx`, or `.ppt`, is placed directly in the To Sort folder, and is not an Office `~$` temporary file. Use **Refresh** to scan the folder again. |
| A scan has a bad title or no metadata | The PDF may be image-only. Run OCR or enter a name manually. |
| Gemini backup is unavailable | Confirm your own key, internet access, Google AI Studio quota/billing, and try Local-only privacy mode. The file can still be sorted manually. |
| Watch & Launch does not wake the app | Open Settings, turn the checkbox off and save, then turn it back on and save. Keep the installed app in a permanent location. |
| A `.doc` file is rejected | Save it as a modern `.docx` file first. |
| An old `.ppt` file has a weak suggestion | This older PowerPoint format is supported cautiously. Review or edit the name, or save it as a modern `.pptx` file for better text extraction. |
| A suggestion for a report/thesis looks odd | This is expected for non-journal material. Review and edit the proposal; the app deliberately avoids inventing journal metadata. |

## Build from source

This path is for people who want to develop or package the app themselves.

```powershell
git clone https://github.com/jacob-aubrey/AI-Powered-Paper-Namer-and-Sorter.git
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

The source repository is [AI-Powered-Paper-Namer-and-Sorter](https://github.com/jacob-aubrey/AI-Powered-Paper-Namer-and-Sorter). It is a real Git repository with `main` tracking `origin/main`.

Before making it broadly public, choose a license and add it to the repository; without one, other people do not automatically have permission to redistribute or modify the code. Please report reproducible issues with the Windows version, input type, naming mode, and a redacted log excerpt—never an API key or confidential document.

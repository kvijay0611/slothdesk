# SlothDesk

A slow, private, overnight file tidier. Point it at a messy folder (like Downloads). A **small
open-weight AI model running on your own computer (through Ollama)** reads each file and files it away:

```
Downloads/bill.pdf  ->  Downloads/Sorted/Finance/2026-10-12_Electricity-bill-October.pdf
```

Nothing leaves your computer. Nothing is deleted or overwritten. Every move is recorded, so
`slothdesk undo` puts it all back. It runs on a plain laptop with **no GPU and no internet**
(after the one-time model download).

Built for the DEV / Hacktoberfest "Slow and Steady" challenge (theme: tools that reward laziness).

## What makes it different

- **Private by design.** Bank statements, ID scans and marksheets are exactly what clutters a
  Downloads folder, and exactly what you should not upload to a stranger's server.
- **Reads scanned documents too.** With `--ocr`, scanned PDFs and photos of certificates are read
  locally (open-source OCR on your CPU), then filed by what they say. No other step needs the cloud.
- **Careful, not clever.** Unsure means "leave it alone". Dry run is the default. Undo is built in.
- **Slow on purpose.** Runs only while plugged in and not busy, at low priority, one file at a time.
- **Measurable.** A built-in evaluation harness tells you how accurate your chosen model really is.

| Need | Local open model | Closed cloud API |
|---|---|---|
| Private files stay private | Yes, files never leave | Contents uploaded |
| Works offline | Yes | No |
| Ongoing cost | Zero | Per-use fees |
| Swap the model | One setting | Vendor's choice |

Honest trade-off: a model small enough for an i3 laptop is less accurate than a giant hosted one.
SlothDesk is built around that fact, and `evaluation/` lets you measure it instead of guessing.

## Setup (Windows, step by step)

Use **Command Prompt**. You need Python 3.11+ and [Ollama](https://ollama.com/download) installed.

```
ollama pull qwen2.5:1.5b
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[pdf,idle,ocr]"
slothdesk doctor --root "%USERPROFILE%\sloth-test"
```

`doctor` should print `OK` lines. The `ocr` extra is optional and large (about 400 MB on my test machine); leave it out if you
do not need scanned files. On macOS/Linux use `source .venv/bin/activate`.

## Everyday use

Always try a **copy** of some files first.

```
slothdesk run   --root "C:\path\to\folder"            (preview only, moves nothing)
slothdesk run   --root "C:\path\to\folder" --apply    (really do it)
slothdesk undo  --root "C:\path\to\folder"            (put everything back)
slothdesk stats --root "C:\path\to\folder"            (what it has done for you)
```

Add `--ocr` to `run` or `watch` to also read scanned PDFs and photos of documents.
Newly changed files (under 2 minutes old) are ignored on purpose, so wait a bit after copying.

Hands-off mode, checking hourly and only when plugged in and idle:

```
slothdesk watch --root "C:\path\to\folder" --apply --ocr --interval 3600
```

| Command | What it does |
|---|---|
| `doctor` | Checks the folder, Ollama, the model and optional extras |
| `run [--apply] [--ocr]` | One pass. Preview unless `--apply` |
| `watch [--apply] [--ocr] [--interval N]` | Repeated polite passes |
| `undo [--run ID]` | Reverses a run (default: the latest) |
| `report [--run ID]` | Prints what a run did |
| `stats [--seconds-per-file N]` | Totals and estimated time saved |
| `purge --days N` | Deletes old history records (never your files) |
| `backup DEST` | Copies the history database |

Exit codes: `0` ok, `1` failure, `2` bad usage or config, `3` model setup unhealthy.

## Choosing and testing a model

Small models run on an i3 with no GPU. Measure them on the built-in synthetic documents. There are two
test sets on purpose:

- **dev set** (`--set dev`, the default): used while improving the prompt.
- **held-out set** (`--set heldout`): never used for tuning. **Report this number**, and run it once,
  so you are not grading the model on the same documents you tuned against (data leakage).

```
ollama pull gemma3:1b
python -m evaluation.run_eval --model qwen2.5:1.5b
python -m evaluation.run_eval --model qwen2.5:1.5b --set heldout
python -m evaluation.run_eval --model gemma3:1b --set heldout
python -m evaluation.compare
```

Known limit: small models are overconfident. In testing, right and wrong answers both came back with
confidence 0.9 to 1.0, so the confidence threshold is a weak safety net. Dry-run-first and `undo` are
the real protection.

`compare` prints one table: accuracy, accuracy on the files it would actually move, how many it
leaves for you, and seconds per file. Pick the best one for your machine and set it with
`SLOTHDESK_MODEL` or `model = "..."` in the config. An optional closed-model baseline is described in
`evaluation/run_eval.py` (it sends only the synthetic samples, never your files).

## What it touches (and does not)

- Looks only at the **top level** of the chosen folder. It does not recurse.
- Skips hidden files, symlinks, partial downloads, very large files and brand-new files.
- Refuses to run on a drive root or your whole home folder.
- Never overwrites: name clashes get ` (2)`, ` (3)`, and so on.
- Sends the model only the first ~1500 characters of text per file, to a model on this machine.
- **File-name hints** come first: obvious words such as `assignment`, `mark sheet`, `transcript`,
  `invoice`, `passport` or `community certificate` file a document without asking the model. This also
  works for scanned files that have no readable text. These rules never use the document's contents.
- Images, videos, installers and archives are filed by extension. With `--ocr`, images that contain
  readable text (like a certificate) are read and filed by meaning; plain photos stay in `Images`.
- Anything it is unsure about, or cannot read, stays where it is and is listed in the report.

Where things go: sorted files land in `<folder>\Sorted\<Category>\`, reports in `Sorted\_Reports\`,
history and logs in `<folder>\.slothdesk\`. Default folders: Finance, Study, Medical, Work,
Identity, Personal, Misc (plus Images, Media, Installers, Archives by extension).

## Configuration

Defaults work out of the box. To customize, copy `config.example.toml` to
`<folder>\.slothdesk\config.toml`. Environment variables (see `.env.example`) override the file.
Unsafe or invalid settings are rejected at startup with a clear message. The model server must be on
this machine unless you explicitly opt in to a remote one.

## Development

```
pip install -e ".[dev,pdf,idle,ocr]"
ruff check src tests evaluation
pytest            # 195 tests, enforces at least 85% coverage
bandit -q -r src
```

Tests use a fake model client and a fake local HTTP server, so they run offline in seconds.
See `docs/` for requirements, architecture, the checklist mapping, security and privacy notes,
and the hackathon plan.

## Status and limits

- Verified: tests pass; the CLI was exercised end to end against a local stand-in for Ollama with
  real OCR on a scanned PDF and a photo of a certificate. Real-model accuracy on your hardware is
  yours to measure with `evaluation/`.
- Measured (small synthetic held-out set, qwen2.5:1.5b on a no-GPU laptop): about 75% of documents
  filed correctly, about 3.7 s per file. On real student files the result was mixed: the model tends to
  put college material under `Work`, which is what the file-name hints partly correct. Treat results
  as indicative, and use preview and `undo`.
- Not done: a GUI, handwriting recognition, learning from your corrections.
- OCR on a slow CPU takes a few seconds per page. It reads only the first pages of a PDF.

MIT licensed.

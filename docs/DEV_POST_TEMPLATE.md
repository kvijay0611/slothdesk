# DEV post outline (fill in with YOUR real numbers)

Use the official template on the challenge page; these notes map to its sections. Writing quality is
the most heavily weighted criterion, so write plainly, in your own voice, and be specific.

**Title idea:** My Downloads folder sorts itself overnight, and nothing leaves my laptop

## What I Built
Who it helps and what effort it saves. One honest paragraph about your own Downloads folder, plus a
before/after screenshot. Mention that it reads scanned certificates and transcripts locally.

## Demo
A 60-90 second screen recording: messy folder, `slothdesk run` (preview), `--apply --ocr`, the
result, `slothdesk undo`. (Optional: narrate it; ElevenLabs is a partner category.)

## Code
Embed your GitHub repo. State that it was created during the challenge window.

## How I Built It
Open pieces: Ollama, the model(s) you used (qwen2.5:1.5b, gemma3:1b), RapidOCR, PDFium. Explain the
design in plain words: rules first, model second, validate everything the model says, journal before
moving, undo, "unsure means leave it alone", low priority and only when plugged in. Say you built it
with AI assistance (Claude) and what you personally decided, tested and changed.

## Why Does Open Innovation Matter?
- Privacy: bank, medical and ID files never leave the machine, including scanned ones.
- Works offline; zero running cost; you can swap the model with one setting.
- Report the **held-out** number (`--set heldout`), and say you tuned the prompt on a different set. Mention the real finding that small models are overconfident, so confidence scores were not a reliable safety net.
- Show the comparison table from `python -m evaluation.compare` and say plainly where a bigger or
  closed model would be better. This is the "where open worked better, and where it did not" the prompt asks for.

## My Agent Session (optional)
Link or embed a saved session if you have one.

## The Lazy Payoff
Paste real output from `slothdesk stats` run on your real folder, plus the assumption behind the
"time saved" estimate. Say how many files it left for you and why.

## Prize Categories
List only categories you genuinely used (for example Gemma, if Gemma was a model you ran and compared).

## Checklist before publishing
- Numbers come from your own runs. Screenshots are readable. README setup works from scratch.
- Repo public, license present, no personal files or secrets committed (check `.gitignore`).

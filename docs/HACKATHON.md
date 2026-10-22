# Hackathon plan: Hacktoberfest Open-Source AI Challenge, Week 3 "Slow and Steady"

Facts below come from the challenge page (https://dev.to/challenges/hacktoberfest-week3-2026-10-19)
and the HF26 hub, read on 22 Oct 2026. Re-check the page; rules can change.

## The rules that matter
- **Deadline: 25 Oct 2026, 11:59 PM PDT** (that is about 12:30 PM IST on 26 Oct). Aim to submit on 25 Oct IST.
- The project **and its repository must be started and completed inside the window (19-25 Oct)**.
  Create a **new GitHub repository** for this; do not reuse an older one. Commits after the deadline
  must be mentioned in the README.
- One submission per challenge; publish a DEV post using the challenge template (tags `devchallenge`
  and `hf26challenge`). Use of AI is allowed. Say in your post that you built with AI help.
- **Judging: Writing Quality is weighted most heavily**, then Relevance to the prompt and theme,
  Creativity, Technical Execution, and (optional) Use of Partner Technology.
- Partner categories are opt-in: use the technology for real, then list the category in your post.

## Your strengths
- Theme fit: "a file and inbox tidier that sorts, renames and archives clutter" is one of the
  suggested ideas, so differentiation matters. Your differentiators: runs on a no-GPU laptop,
  reads scanned documents locally, "unsure means leave it alone", undo, and honest measurements.
- The post template has an optional **"The Lazy Payoff"** section. Use `slothdesk stats` on your
  own real Downloads folder to fill it with real numbers.

## About "free NVIDIA / hosted resources"
- NVIDIA is not one of the listed partners on the challenge page. Free partner credits listed there:
  Tinker, Render, Backboard, and a free .tech domain (claim at hacktoberfest.com/my/promos).
- **Do not send your real files to any hosted service.** That would break your core claim.
  Hosted models are fine for one thing: running the **evaluation on the synthetic sample set**
  (`python -m evaluation.run_eval --provider openai --base-url <endpoint> --model <name>`) to show
  how a larger open-weight or closed model compares. I have not checked any provider's free-tier terms.

## Partner categories worth considering
| Category | Prize | Fit | Effort |
|---|---|---|---|
| **Gemma** | $200 | Use Gemma through Ollama as the sorting model (`gemma3:1b`) and compare it with Qwen using `evaluation/`. Only enter if you genuinely use it. | Low |
| **ElevenLabs** | $100 | "Generate narration for your demo". Optional polish for the video. | Low |
| **.tech domain** | $100 | Needs a live site on a claimed domain (for example a one-page GitHub Pages landing page). | Medium |
| **Tinker** | $200 | Fine-tune a small model for filing and show improvement over a baseline. Strong story, but needs labelled training data, cloud training, and getting the result back into Ollama. Risky with days left; treat as a stretch only. | High |
| Backboard | $100 | Compare open-weight models through one API key. Overlaps with the evaluation idea; optional. | Medium |

Skip the rest (they need web apps, databases, auth or agents that SlothDesk does not have).

## Suggested schedule (IST)
- **Thu 22 Oct:** finish setup. Run `evaluation.run_eval` for `qwen2.5:1.5b` and `gemma3:1b`; run `evaluation.compare`.
  Create the new GitHub repo and push the project (first commit inside the window).
- **Fri 23 Oct:** try `--ocr` on a copy of your real files. Run `slothdesk stats`. Fix anything odd.
  Record the demo video (messy folder, preview, apply, undo; with OCR on the scanned transcript).
- **Sat 24 Oct:** write the DEV post (see `DEV_POST_TEMPLATE.md`). Get a friend to read it.
- **Sun 25 Oct:** final proofread, check the repo README, publish well before the deadline.

## Honesty rules for the post (they also help the score)
- Report your own measured numbers, including where the local model loses.
- Say the sample set is small. Say OCR is slow on your CPU if it is.
- Do not claim accuracy you did not measure. Do not claim partner use you did not do.

# Sim Grader Copilot (SGC)

SGC helps instructors grade student reflections on nursing lab simulations. It reads each submitted assignment (PDF or DOCX), scores it against a four-category rubric, flags anything unusual, and produces a single PDF **Grade Recommendation Report** for the instructor to review.

SGC makes *recommendations*. The instructor remains the final authority on every grade.

## Principles

- **Rubric fidelity.** Scores are based only on the rubric, applied objectively.
- **Parsing accuracy.** If a submission cannot be read reliably, SGC refuses to grade it and says so, rather than guessing.
- **Transparency.** Every score is accompanied by evidence from the student's text so the instructor can verify it.
- **Flag, don't judge.** Suspicious content (blatant AI artifacts, prompt injection, profanity, fourth-wall breaks, off-topic text) is surfaced for the instructor. SGC does not accuse students or attempt general AI detection.
- **De-identification.** Before any text is sent to a language model, student names from a list you provide are removed programmatically (not by a model). Only the names on the list are removed; other identifying details are not.

## What it does

SGC is run from the command line. Download all submissions into one directory, then point SGC at it with the rubric and a list of student names. Illustrative example (final syntax TBD):

```
sgc grade ./submissions --rubric rubric/simulation_reflection_rubric.md --names names.txt
```

Each student's section in the report is labeled with the submission's filename. The report PDF is saved in the same directory as the submissions.

For each submission, SGC:

1. Loads the rubric.
2. Loads the assignment, removes listed student names, and checks for prompt injection.
3. Reads the assignment.
4. Scores each rubric category (integer, per the rubric's levels) and totals the score.
5. Flags whether the word count is under 500 or over 1000.
6. Adds an entry for the student to the report.

Expected volume is roughly 10-20 assignments per week. Speed is not a priority; quality is.

## Seeing what is sent to the model

SGC logs every request it makes to the language model, exactly as sent, along with the response. Look in the `sgc_run_<timestamp>/` folder next to the report: each submission has an `llm_calls.md` (readable) and `llm_calls.jsonl` (exact). Only redacted text appears in these logs, never filenames or the API key. To inspect the redacted text and the first prompt before anything is sent, run with `--dry-run`.

## Building the names file

`scripts/extract_names.py` finds likely student names in a folder of submissions without using any language model (labels such as "Name:", document properties, headers, the first lines, and file names). Names that appear in many submissions, such as the simulated patient or the instructor, are listed separately and left out.

```
.venv/bin/python scripts/extract_names.py ./submissions
```

It writes `names_candidates.txt`. Review it, add anyone it missed (files with no confident name are listed), and copy the names into `names.txt`. Lines starting with `#?` are low-confidence guesses. Both files hold real names; keep them out of git.

## Setup: OpenAI API key and billing

SGC uses the OpenAI API. A ChatGPT subscription does **not** include API access, so you need a separate API account with billing. These steps are done once (the OpenAI dashboard layout changes from time to time, so menu names may differ slightly):

1. Go to [platform.openai.com](https://platform.openai.com) and sign in or create an account. This is separate from chatgpt.com, though you can use the same email.
2. Under **Settings → Billing**, add a payment method and add prepaid credit (for example $10; usage is small, see below).
3. Create a dedicated **project** for SGC (for example, "Sim Grader"), so its usage and key are isolated.
4. In that project's limits settings, set a **monthly budget of $25**. This is the hard cost backstop. Consider turning on email alerts at a lower threshold (for example $10).
5. Create an **API key** for the project (**API keys → Create new secret key**). Copy it immediately; it is shown only once.
6. Make the key available to SGC as an environment variable. On macOS, for the current terminal session:

   ```
   export OPENAI_API_KEY="sk-..."
   ```

   To make it permanent, add that line to `~/.zshrc`. Never commit the key to the repository or share it.

Expected cost is roughly $3 per week at 20 submissions with the default model (an estimate, to be measured). SGC records actual token usage and estimated cost for each run, and can enforce a per-run ceiling with `--max-spend`.

**Data handling:** OpenAI does not train on API data by default. SGC requests that responses not be stored (`store=false`), but OpenAI keeps abuse-monitoring logs, which can include prompts and responses, for up to 30 days. SGC removes the student names you list before sending anything.

## Status

In development. Working now: rubric parsing, file discovery, PDF and DOCX extraction with a quality gate, word count, name redaction, `--dry-run`, the OpenAI client with full request logging, screening (code checks plus a model pass), grading (1 sample by default; `--samples 3` or more for a median and Borderline from disagreement), the perfect-score suggestion, spend tracking, and the PDF Grade Recommendation Report. Tested with a scripted fake model only; no live API call has been made yet. A real run writes the report next to the submissions, plus `result.json` per submission in the run folder.

To try the working part (Python 3.9 or newer):

```
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/sgc grade ./submissions --rubric rubric/simulation_reflection_rubric.md --names names.txt --dry-run
.venv/bin/pytest
```

A dry run writes `sgc_run_<timestamp>/` next to the submissions, with the redacted text of each file (`S001/redacted.txt`). Read a few before trusting the redaction, and make sure your real names list is in place first.

Current documents:

| Document | Purpose |
| --- | --- |
| [plans/draft_spec.md](plans/draft_spec.md) | Original draft notes |
| [plans/product_spec.md](plans/product_spec.md) | Product requirements |
| [plans/technical_spec.md](plans/technical_spec.md) | Technical design |

Still to come: evaluation set.

## Repository layout

```
sgc/                    The tool
tests/                  Unit tests (no network needed)
plans/                  Specs and planning documents
rubric/                 Grading rubric (simulation_reflection_rubric.md)
names.txt               Names to remove from submissions (one per line)
scripts/extract_names.py  Local helper that proposes names for names.txt
example_assignments/    Real student work (git-ignored, never commit)
```

# How-to voice reference

Reference for writing the launch use-case guides. The target register is the plain
Hugging Face tutorial voice: **state what the reader will do, then do it.** No pain
setup, no narrative framing, no momentum padding.

The canonical exemplar is the `huggingface_hub` **Search the Hub** guide
(<https://huggingface.co/docs/huggingface_hub/en/guides/search>), pulled verbatim
2026-06-29. Read it before writing. The notes below are extracted from it.

## The opening

Lead with the reader's **goal in their own terms**, then the approach. HF opens with the
goal verb — "search the Hub" — not the mechanism ("call `list_models()` with `filter`"):

> In this tutorial, you will learn how to search models, datasets and spaces on the Hub
> using `huggingface_hub`.

No problem statement, no "you wanted...", no running-example backstory. For our guides:
name the goal and two or three of the reader's own tasks ("such as ..."), then say how in
one compressed sentence ("We will do this by ...") with a light payoff ("in just a few
lines of code"), then the pattern:

> In this guide, you will learn how to find the right model size for a repeated task, such
> as a classifier, an extractor, or a routing step. We will do this by rerunning the exact
> same task under a set of models, comparing each one's answers against a known-good
> baseline, and keeping the cheapest model that still holds up, in just a few lines of code.
> This is Shepherd's *Model Right-Sizing* pattern.

The failure to avoid is opening with the **mechanism**. "run one model step at several
model scales over identical inputs, grade each against a ground-truth oracle" describes our
procedure; "find the right model size for a repeated task" names what the reader wants. A
reader responds to the second, not the first. "We will do this by ..." is allowed and
useful — it states the approach, which is not the banned momentum padding (that narrates
the reader's experience: "watch the story unfold").

## The cadence

State what a thing is, end on a colon, show the code:

> `huggingface_hub` library includes an HTTP client `HfApi` to interact with the Hub.
> Among other things, it can list models, datasets and spaces stored on the Hub:

Explain results as flat facts:

> The output of `list_models()` is an iterator over the models stored on the Hub.

> Similarly, you can use `list_datasets()` to list datasets and `list_spaces()` to
> list Spaces.

Introduce the next step plainly. "you" is functional, never emotional:

> Listing repositories is great but now you might want to filter your search.

> While filtering, you can also sort the models and take only the top results. For
> example, the following example fetches the top 5 most downloaded datasets on the Hub:

Point onward in one line:

> For more details, see the [CLI guide](./cli#hf-models).

## What the voice does

- **Opens with the deliverable, not a problem.** "In this guide, you will learn how to
  X." Then start.
- **States facts flatly.** "The output of `list_models()` is an iterator over the
  models stored on the Hub." No emphasis, no hedging, no reassurance.
- **Instructions are imperative, verb-first.** "visit [models] and [datasets] pages in
  your browser, search for some parameters and look at the values in the URL."
- **Code follows a plain sentence ending in a colon.** The prose says what the code
  does; the block does it.
- **"you" is functional only.** "you can use", "you might want to". The warmth ceiling
  is "Listing repositories is great but..." — that is as far as it goes.
- **Short sentences. Few or no em-dashes.** A period or a "because" clause does the job.
- **Section headings are plain.** "How to list repositories?", "Using the CLI". Short
  noun or task phrases, not claims.

## What the voice never does

- **No pain or narrative setup.** Not "Your agent hands you one answer and moves on",
  not "you wanted to see a few attempts side by side". Name the task.
- **No projected reader backstory.** No "you wanted...", "imagine...", "you might be
  picturing...".
- **No momentum padding.** No "we'll run it in three steps so you can watch the story
  unfold", no cliffhanger questions ("But can we go further?", "see if you can spot it").
- **No performed significance.** No "That matters:", "and nothing else", "That's the
  whole test", no emphatic colons used for drama.
- **No antithesis as a tic.** One "X, not Y" is fine. "tells you *that* X, not *what* Y"
  on every beat reads as machine-generated.
- **No reassurance or permission.** No "don't worry", no "if that's all you came for",
  no "you don't need any other notebook to follow along".

## Before -> after (from these guides)

- "Your agent hands you one answer and moves on — but you wanted to see a few attempts
  side by side, keep the one that holds up..."
  -> "In this guide, you will learn how to run several attempts at one task, check each
  with plain gates, keep the strongest, and keep a record of the rest."

- "So which attempt is broken, and who says so without a human ranking them? Not a
  model-as-judge — plain Python you can read."
  -> "Each attempt is checked by plain Python, not a model."

- "The first step writes a plan and nothing else. That matters: the plan is the *good
  prefix*, the part of the run that was fine before anything drifted."
  -> "The first step only writes a plan. That plan is the *good prefix*, the part of the
  run that was fine before anything drifted."

- "It's objective and nearly free — exactly what you want as the thing an expensive
  model is measured against."
  -> "It is objective and nearly free, which is what you want to grade an expensive
  model against."

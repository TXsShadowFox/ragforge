# Evaluation

How well does RAGForge find the right text, and how good are its answers? Measured on
sample documents, with the real models. The latest report: [RESULTS.md](RESULTS.md)
(numbers: [results.json](results.json)).

## The test set

- [corpus/](corpus): 5 documents of a made-up college, Northfield College (about 2,700
  words): a 6-page PDF handbook, a library guide and campus services page in Markdown,
  housing rules in HTML, and an IT help FAQ in plain text. They have many specific facts
  (fees, times, rooms), some of them close to each other on purpose (a late book costs 5
  rupees a day, a late laptop 50 rupees an hour, a lost key 400 rupees).
- [questions.json](questions.json): 48 questions.
  - 36 have an answer in the documents. Each names its document and an **evidence** phrase:
    a short exact piece of the text that holds the answer. Kinds: `lookup` (the question
    uses the document's words), `paraphrase` (other words: "copies" for "plagiarism",
    "installments" for "instalment plan"), `exact` (names and numbers like `NFC-Secure`),
    and `near-miss` (a similar fact elsewhere must not win).
  - 12 have no answer there; the right reply is "I don't know". 8 are on topic
    (`not-in-documents`), some close on purpose: the guest Wi-Fi's *password* (only its
    name is there). 4 are off topic (`off-topic`: "What is the capital of France?").

A unit test checks that every evidence phrase really is in its document after our own
parsing, so the test set cannot silently break.

## What it measures

**Search quality**, for several settings (no LLM needed):

- **hit rate@k:** the share of questions with a relevant chunk in the first k results. A
  chunk is relevant if it contains the question's evidence, so the same test works for any
  chunk size.
- **MRR@10:** the mean of 1/place of the first relevant chunk (0 if not in the first 10).
- **The "I don't know" gate:** how often no chunk reaches `MIN_RERANK_SCORE`, so the LLM is
  not asked, for questions with and without an answer; also for other thresholds, from
  each question's best reranker score.

Settings: hybrid search with and without the reranker, vector search only, keyword search
only, and chunks of 300, 500 and 1000 tokens. The embedder (bge-small) reads at most 512
tokens, so 1000-token chunks are embedded from their first half only: the evaluation
shows what that costs. (The worker refuses such chunks; the evaluation indexes the
documents itself, with the same parsing, chunking and embedding code.)

**Answer quality**, with the API's setting (the same steps as `POST /v1/chat`, without the
answer cache): the real LLM answers, and a bigger LLM, the **judge**
(`openai/gpt-oss-120b`), reads the question, the reference answer, the sources and the
answer, and says:

- **faithful:** is every fact of the answer in the sources? (yes 1, partly 0.5, no 0)
- **correct:** does it give the facts of the reference answer? ("I don't know" counts as 0)

For the 12 questions without an answer, it counts how often the reply is "I don't know".

## Latest results (2026-10-08)

All numbers: [RESULTS.md](RESULTS.md).

- **Search:** the API's setting (hybrid search + reranker) ranks the evidence first for 97%
  of the questions (MRR@10 0.986). Without the reranker: 89% (0.940); vector search alone,
  without the reranker: 86% (0.904). Chunks of 300 or 1000 tokens: 92% and 94%, so 500
  stays the default.
- **Answers:** faithfulness 1.00 and correctness 1.00 (judged by `gpt-oss-120b`); 36 of 36
  answered, and "I don't know" for 12 of 12 questions without an answer.
- **A small corpus:** the 5 documents make only 14 chunks, fewer than the 20 candidates of
  each search, so every setting has the evidence in its top 5. Only hit@1 and MRR show the
  differences.

What the evaluation found (all fixed):

1. gpt-oss sometimes cites as `【1】` instead of `[1]`, so those answers had no citations.
   They are now rewritten as `[1]` before the citations are read.
2. The "I don't know" gate (`MIN_RERANK_SCORE=-5`) wrongly stopped 5 of 36 answerable
   questions (correctness 0.83). Off-topic questions score about -11, and the lowest
   answerable one -9.7, so the gate is now -10: it stops no answerable question
   (correctness 1.00), and off-topic questions still cost no LLM call. The price: more
   chunks pass, so an answer reads 1,442 input tokens instead of 747.
3. The PDF builder of the tests garbled apostrophes (found by the test-set check).

## Run it

Docker must be running, and `.env` must have the LLM settings (`LLM_API_KEY`).

```
make eval                                   # everything, ~10 min (Groq's free tier is slow)
uv run python -m eval.run --no-answers      # search quality only: ~2 min, no LLM
uv run python -m eval.run --limit 5         # a quick try with 5 questions
```

Postgres and Qdrant run in throw-away containers (like the integration tests), so your
dev data is never touched. On Groq's free tier, answers and judgments wait when the
tokens-per-minute limit is reached; the run takes longer but finishes.

`tests/integration/test_eval.py` runs the whole evaluation with fake models, so the code
keeps working between real runs.

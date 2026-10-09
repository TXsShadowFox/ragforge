# Load tests

k6 scripts for the chat (answers from the cache, and new questions) and the upload
endpoint. The latest numbers, what they found and what was fixed: [RESULTS.md](RESULTS.md).

## What is here

- `chat_cached.js`: 12 questions asked again and again; each was asked once in `setup()`,
  so every answer comes from the cache.
- `chat_uncached.js`: a new question every time, so each one runs the whole RAG path.
  `STREAM=true` asks for streamed answers (like the widget): "first byte" is then the time
  until the answer starts.
- `upload.js`: a new small text file every time; `teardown()` measures how long the worker
  needs to process them all.
- `lib.js`: a fresh tenant with documents (the evaluation's 5 plus 4 generated ones), the
  questions, the load steps and the results table.
- `fake_llm.py`: an OpenAI-compatible LLM that answers after a fixed delay
  (`FAKE_LLM_FIRST_TOKEN_SECONDS`, `FAKE_LLM_TOKENS_PER_SECOND`). Groq's free tier allows
  ~30 requests a minute, so the real LLM cannot be load-tested. CI's browser test uses it
  too.

## Run it

`make loadtest` starts the stack with the fake LLM (`docker-compose.fake-llm.yml`: higher
rate limits, no semantic cache, no tracing) and runs the three tests one after the other.
Each prints a table and writes it, with all of k6's numbers, to `results/`.

One test, with your own steps:

```
docker compose -f docker-compose.yml -f docker-compose.fake-llm.yml run --rm \
  -e USERS=1,5,10 -e SECONDS=30 k6 run chat_uncached.js
```

`USERS`: the steps (that many users at the same time, each sends its next request as soon
as the last one is answered); `SECONDS`: the length of each step. Prometheus
(http://localhost:9090) has the time of each search step during the test. Stop everything
with `make down`.

Everything runs on one machine, so compare runs on the same machine (before and after a
change), not with a real server.

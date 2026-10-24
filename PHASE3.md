# Phase 3 usage

Set `.env`: `A2A_DEMO_MODE=false`, `AI_PIPE_TOKEN=<your own token>`, and `AI_PIPE_MODEL=gpt-4.1-nano`. Never commit `.env`.

Run `python -m pytest -q`, then `python -m uvicorn app.main:app --reload`.

Send a batch with actual invoice packages; empty package batches return HTTP 422 in live mode. The AI response is validated against source bracketed references and persisted in a proposal artifact with `TASK_STATE_INPUT_REQUIRED`.

## Known limitations / next work

- This is Phase 3, not a grader-ready release. Continuations return 501 until Phase 4.
- A single model call covers uncached packages. Invalid output returns 422 and does not create a Task. There is no automatic retry or repair pass yet.
- Cache keys include policy revision and canonical package content. Exact repeated messages replay their Task; concurrent first-time requests can still trigger duplicate model calls because the model call occurs before the SQLite atomic save. Add a durable in-flight reservation in Phase 4.
- Evidence validation checks exact bracketed refs in package content, but cannot prove they come from the decisive paragraph. This requires a representative grader batch and accuracy tests.
- The model endpoint is configurable and has not been tested with a real AI Pipe token in this environment.
- Do not deploy using SQLite on an ephemeral filesystem for production persistence.

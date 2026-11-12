# WorkMind load tests

Locust scenarios for WorkMind AI, a stub model server, and a report generator. The full guide
is [docs/load-testing.md](../docs/load-testing.md).

```bash
uv sync
make -C .. loadtest USERS=5 DURATION=60s NO_MODEL=1 LABEL=smoke   # no model calls
make -C .. loadtest-stub                                         # the stub, on :8490
make -C .. loadtest-check                                        # lint, types, tests
```

| Path | |
|---|---|
| `locustfile.py` | Entry point for Locust (`uv run locust --host http://localhost:8480`) |
| `workmind_load/` | Users, tokens, preflight, SSE timing, report, the run script |
| `stub_llm.py` | The stub model server (OpenAI-compatible, configurable timing) |
| `compose.stub.yaml` | Points the stack's model roles at the stub |
| `results/` | Markdown reports (committed); `results/raw/` holds Locust's output (ignored) |
| `.secrets/` | Plain access tokens between `tokens create` and `tokens revoke` (ignored) |

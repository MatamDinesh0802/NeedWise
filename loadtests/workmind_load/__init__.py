"""WorkMind AI load tests (docs/load-testing.md).

settings.py   what a run is configured with (Locust options, environment variables)
questions.py  question sets taken from evals/datasets, so answers hit real documents
browser.py    the Keycloak sign-in a browser does, for session-only endpoints
tokens.py     creates and revokes the demo users' access tokens; cleans up after a run
sse.py        reads a run's Server-Sent Events: time to first token, total time
users.py      the Locust users: one class per kind of person and what they do
preflight.py  checks the target before a run (health, which model each role uses)
report.py     turns Locust's CSV/JSON stats into a Markdown report
run.py        one run end to end: tokens, Locust, revoke, report (`make loadtest`)
"""

# LinkedIn post #5: Same question, two people, two answers

- **Status:** Draft, not posted
- **Milestone:** Phase 4 (streaming chat with citations)
- **Image:** `assets/post-05-same-question.png` (1080×1350). The two screenshots are the real app on the synthetic Acme Robotics company (`assets/post-05-alice-chat.png`, `assets/post-05-mallory-chat.png`). Numbers come from `evals/results/2026-11-04-qa-auto.md` (answers) and `2026-11-04-baseline.md` (red team).
- **Best with video:** a 60–90 s screen recording of "view as" Alice → Mallory. LinkedIn favours native video; the image works as the fallback or second slide.
- **Post only after** the repo is public.
- **Before posting:** re-run the red-team suite on the current commit. The "0 leaks across 16 attacks" result is from commit `518a21f`, before the chat and agents landed.

---

## Post

Most enterprise AI demos show one person asking one question.

The interesting part is what happens when someone else asks it.

I gave the same question to two people at the same (fictional) company: "How many weeks of parental leave do we get?"

→ Alice, HR manager: "16 weeks, fully paid", with a citation chip on every claim. Click one and the exact passage of the 2026 policy opens next to the answer, labelled "official". It also points out the outdated 2025 draft and a Slack message claiming 20 weeks, and says which one counts.

→ Mallory, an external contractor: "I couldn't find this in the sources available to you." No hint that a leave policy exists, and the answer model is never even called.

Same code, same model, same question. The difference is decided before the model sees anything: search only returns passages the person may open, and a second permission check runs before the answer is written.

What's in the chat now:
• Answers stream word by word, and the sources arrive first
• [S1]-style citations become chips that open the exact passage
• Reload in the middle of an answer and it picks up where it was, without repeating a word (every event has an id)
• Thumbs up or down with a comment on every answer
• A "view as" switch for demos: the same workspace through the eyes of 8 different employees

The bug I'm proudest of finding: "view as" silently logged me back in as the previous person. Logging out of the app isn't logging out of the identity provider. With its session still alive, it re-authenticated the old user and ignored which account I asked for. Logout has two halves.

On 82 test questions: 80 answers contain every required fact, a median of 6 seconds to a complete answer, about 2.5 cents each. 0 leaks across 16 attacks, with canary tokens hidden in restricted documents.

Would you trust an AI assistant more if it showed you exactly where every answer came from?

Repo and a short demo in the first comment.

#AIEngineering #RAG #OpenSource #EnterpriseAI #BuildInPublic

---

## First comment

Repo (Apache-2.0): <GITHUB_REPO_URL>
90-second demo, Alice vs. Mallory: <DEMO_VIDEO_URL>
How permissions are enforced (two locks): <GITHUB_REPO_URL>/blob/main/docs/ARCHITECTURE.md#7-permissions-authorization
Evaluation results: <GITHUB_REPO_URL>/tree/main/evals/results

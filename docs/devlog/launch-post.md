# LinkedIn launch post: NeedWise v1.0

- **Status:** Draft, not posted. This is the one post for the launch; posts 1 to 13 are the development log.
- **Numbers:** from the v1.0.0 release check ([benchmarks](../benchmarks/v1.0.md)) and the final test runs of 2026-11-13 (1,967 backend, 1,197 web and 184 browser tests).
- **Video:** upload `docs/media/demo.mp4` (1 min 32 s, recorded on 2026-11-13 with Gemini 3.1 Flash Lite) directly to LinkedIn. `docs/media/demo.gif` is the fallback.
- **Before posting:**
  - the repo is public and the v1.0.0 release is published
  - "10 days" is true for you. Git history: first commit 2026-11-04, last 2026-11-13.

---

## Post

Ask your company's AI assistant for the salary bands. Should it answer?

It depends on who's asking.

That rule shaped everything in NeedWise, the open-source enterprise AI assistant I'm releasing today as v1.0.

In the video, Alice from HR asks for the L3 salary band and gets it, with a citation she can click. Bob, an engineer, asks the exact same question and is told his sources don't contain it. Same app, same model, same question. The permission check happens before the model sees a single document.

What it does:
→ Answers from company documents, with a citation on every claim, using only what that person may open
→ Department agents (HR, Finance, Engineering, Legal) that an admin adds without code
→ Drafts actions like emails, but a person approves before anything leaves
→ Turns meeting recordings into decisions and action items with clickable timestamps
→ Answers questions about data with a chart and the SQL behind it
→ Admin tools: usage, budgets, audit log, retention and legal hold (some in the Enterprise edition)
→ Self-hosted: runs with Claude, Gemini or fully local models

Measured, not claimed (Claude Sonnet 5.5, on a fictional demo company):
• 0 permission leaks across 68 attacks. A single leak fails CI.
• 81 of 82 answers had every required fact, and 6 of 6 times it said "I don't know" when the documents didn't have the answer
• First words in 0.9 s (median), about $0.007 per answer
• 3,300+ automated tests, 184 of them in a real browser

I built it in 10 days with Claude Code as my pair programmer. I made the design decisions and set the bar. Claude wrote most of the code, and nothing merged unless the tests and leak checks passed. My biggest lesson: when an AI writes code this fast, the checks that catch it being wrong matter more than the features.

What it isn't yet: it has only run on a made-up company, and Google Drive and Slack sync still need testing against real workspaces. If you try it on real data, I'd love your feedback.

What would you need to see before trusting an AI assistant with your company's documents?

The repo and the full benchmarks are in the first comment.

#AIEngineering #OpenSource #EnterpriseAI #RAG #ClaudeCode

---

## First comment

Repo (Apache-2.0 core): https://github.com/MatamDinesh0802/NeedWise
Quick start, three commands to a cited answer: https://github.com/MatamDinesh0802/NeedWise#quick-start
Benchmarks and how they were measured: https://github.com/MatamDinesh0802/NeedWise/blob/main/docs/benchmarks/v1.0.md

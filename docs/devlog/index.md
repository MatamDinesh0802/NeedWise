# Devlog

NeedWise is built in public, one phase at a time ([Roadmap](../ROADMAP.md)). Each milestone gets
a short post that shows something real: a diagram, a measured number, the app itself. These are
the drafts. None has been posted yet.

Every number in a post comes from a committed evaluation report or a measured page of these
docs, and every image is generated from the code or the running app (the images and their HTML
sources are in `docs/devlog/assets/`). Posts 7 to 13 suggest a visual instead: their images are
not made yet. The links in the posts' first comments point to the repository,
`https://github.com/MatamDinesh0802/NeedWise`, and open once it is public.

**For the launch, one post:** [the launch post](launch-post.md). The app screenshots in posts
1, 2 and 5 were taken at those milestones, when the project was still called WorkMind. The thirteen below are the
development log, one per milestone.

| Post | Milestone | About |
|---|---|---|
| [#1 Building in public](post-01-building-in-public.md) | Phase 0: foundation | Who is allowed to see what, as the problem to build around |
| [#2 Who is allowed to see what?](post-02-who-can-see-what.md) | Phase 1: identity and permissions | The permission model as a grid of tested assertions |
| [#3 From upload to answer](post-03-upload-to-answer.md) | Phase 2: ingestion | What happens between "upload PDF" and an AI answer |
| [#4 Textbook hybrid search made retrieval worse](post-04-hybrid-search-measured.md) | Phase 3: retrieval and evaluation | Rank fusion settings, measured |
| [#5 Same question, two people, two answers](post-05-same-question-two-people.md) | Phase 4: cited chat | The same question as an HR manager and as a contractor |
| [#6 Do you need multi-agent? I measured it.](post-06-multi-agent-measured.md) | Phase 5: department agents | Department agents against a single search |
| [#7 Your company knowledge inside your IDE](post-07-company-knowledge-in-your-ide.md) | Phase 6: tools and MCP | NeedWise as an MCP server, with the asker's permissions; the research agent's attacks |
| [#8 Keeping permissions in sync is the hard part](post-08-keeping-permissions-in-sync.md) | Phase 7: connectors | Documents' own access lists, identity mapping that fails closed, the side paths a review found |
| [#9 When should the AI ask permission?](post-09-when-should-ai-ask-permission.md) | Phase 8: approvals and workflows | Side-effect levels, approver rules, request routing measured |
| [#10 From a meeting recording to tracked commitments](post-10-meeting-to-commitments.md) | Phase 9: meetings | Local speech-to-text, action items with owners, the name hint |
| [#11 Letting an LLM write SQL, safely](post-11-llm-writes-sql-safely.md) | Phase 10: business intelligence | Row policies in code, and the error-message leak a review found |
| [#12 A new agent without code](post-12-new-agent-without-code.md) | Phase 11: admin and governance | The Legal agent made in the browser, and the plan it broke |
| [#13 v1.0: what I learned](post-13-v1-what-i-learned.md) | Phase 12: v1.0 | Five lessons, the release numbers, what is not done |

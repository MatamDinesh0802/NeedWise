# LinkedIn post #1: Building in public

- **Status:** Draft, not posted
- **Milestone:** Phase 0 complete (v0.0.x)
- **Images:** `assets/post-01-architecture.png` (1080×1350, first), `assets/post-01-app.png` (second)
- **Post only after** the GitHub repo is public, because the first comment links to it.

---

## Post

Most enterprise AI demos skip the hardest question:
Who is allowed to see what?

Ask a company AI assistant "What's Priya's salary band?"
An HR manager should get the answer.
An engineer should get "I couldn't find that in sources available to you." Not even a hint that the document exists.

That's the problem I'm building around. I'm building NeedWise, an open-source AI workspace for companies, and I'm doing it in public.

The idea: employees ask questions in plain language and get answers from company documents, with citations, drawn only from sources they are allowed to see. Specialized agents (HR, Finance, Engineering) handle department work. Anything that leaves the system needs human approval.

Four rules I'm holding myself to:
1. Permissions are enforced by code, never by the LLM
2. Every claim cites a source, or the assistant says "I don't know"
3. Measure, don't claim: retrieval quality, leak rate, latency and cost, published with every release
4. Self-hosted and model-agnostic: run local models or plug in paid APIs

Phase 0 is done. One command starts 8 services (PostgreSQL, Qdrant, Keycloak, OpenFGA, Valkey, FastAPI, Next.js and Caddy), all health-checked end to end. The app's own database role can never bypass row-level security, so tenant isolation won't depend on the application code being perfect.

Next: login, multi-tenancy and the permission model. To test it, I've created a fictional company, Acme Robotics, with six personas. One of them is an external contractor whose only job is to try to see things they shouldn't.

The roadmap has 13 phases to v1.0. I'll post each milestone here, including the numbers that don't look good.

What would make you trust an AI assistant with your company's documents?

Repo link in the first comment.

#OpenSource #AIEngineering #RAG #LLM #BuildInPublic

---

## First comment

Repo (Apache-2.0): https://github.com/MatamDinesh0802/NeedWise
Architecture, end to end: https://github.com/MatamDinesh0802/NeedWise/blob/main/docs/ARCHITECTURE.md
Roadmap: https://github.com/MatamDinesh0802/NeedWise/blob/main/docs/ROADMAP.md
A ⭐ helps if you want to follow along.

---

## Image alt text

1. **Architecture:** Architecture diagram of NeedWise. Browsers and MCP clients enter through Caddy. The application layer has Next.js, FastAPI and Keycloak. A planned layer has a supervisor agent with HR, Finance and Engineering agents and permission-aware retrieval. The data layer has PostgreSQL, Qdrant, OpenFGA and Valkey running today, with LLMs planned. Four design principles are listed below the diagram.
2. **App:** Screenshot of the NeedWise web app's Overview page. A system status card shows all five components healthy: PostgreSQL, Valkey, Qdrant, OpenFGA and Keycloak.

---

## Before posting

- [ ] GitHub repo is public and the README renders correctly
- [ ] Check the repository link in the first comment (https://github.com/MatamDinesh0802/NeedWise)
- [ ] Upload the architecture image first, then the app screenshot
- [ ] Add the alt text to both images
- [ ] Post the first comment right after publishing, and reply to early comments

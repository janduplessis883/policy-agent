# Nathan design

Approved in chat: Python, Streamlit, direct Notion API, OpenAI-compatible Chat Completions.
Endpoint: https://freellmapi-4ace.srv1395197.hstgr.cloud/v1, model auto.
Secrets: FREELLMAPI_SPI_KEY, NOTION_TOKEN, ACCESS_PIN. Each tool has its own Python file.

Every request establishes Europe/London date, retrieves the tagged Surgery Profile,
then lists all policy records and reads relevant content. Profile failure stops the request.
User amendment: future Review_Date establishes approval/currency; no separate status needed.
Draft, placeholder, archived and superseded documents remain ineligible for live instructions.
No workflow narration in staff answers. Citations use supplied public URLs only.

Answer/review/gap operations use structured, validated model output. Python determines currency,
restricts sources and write fields, and rejects incomplete reads for definitive gap decisions.
Gap decisions read the entire accessible policy collection. No external verification tool is
included initially: external compliance and mandatory-policy claims must be flagged unverified.

Confirmed gap proposals require an explicit user instruction that written coverage is needed.
A Create placeholder button authorises the specific write. Refresh and reassess sources before
creation, serialize writes within the server process, never retry an uncertain write automatically,
and verify page properties and empty children. No templates or extra properties are sent.

PIN gate precedes all external requests, has process-wide failed-attempt throttling, and logout
clears session data. Shared PIN provides shared access, not individual identities. Deploy one
server worker behind HTTPS; multi-worker/distributed auth and write locks are outside this scope.

Alternatives considered: LangChain/LangGraph; direct Python chosen for fixed ordered checks and
compatibility without native model tool calling. No vector store, persistent chats or patient data.
Original playbook retained separately; these approved amendments take precedence.

## Follow-up requirements

User requested genuine streamed model output and an indicator of the tools called.
Live source tool names are now shown by the UI, overriding the earlier silent-UI rule;
answers still omit internal reasoning and raw tool payloads. The final assessment is
validated before it is saved and before any write proposal is made available.

## Markdown content migration

User requested the page Markdown endpoint for all policy and Surgery Profile content.
Use Markdown v2 and Notion-Version 2026-03-11 for content retrieval, recover unknown
subtrees where readable, and preserve incomplete-source safeguards. Other metadata
and write operations retain their existing routes. Placeholder empty-body verification
continues using block children.

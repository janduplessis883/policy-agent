# Nathan — Stanhope Mews Surgery policy assistant

Python + Streamlit chat, direct Notion API, and the OpenAI Python client configured for:

- Base URL: `https://freellmapi-4ace.srv1395197.hstgr.cloud/v1`
- Model: `auto`
- Notion data source: `261fdfd6-8a97-81b1-9e23-000bf4245172`
- Database: `261fdfd6-8a97-8060-af8a-f3f29754e96e`

## Run locally

Use Python 3.12 or newer. From the project folder:

```bash
uv sync
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Fill in the three quoted values in `.streamlit/secrets.toml`:

```toml
ACCESS_PIN = "your-pin"
FREELLMAPI_SPI_KEY = "your-api-key"
NOTION_TOKEN = "your-notion-integration-token"
```

The spelling `FREELLMAPI_SPI_KEY` is intentional. Secrets are excluded from git.
Give the Notion integration access to the Policies database, including read and insert
content permissions. It needs access to the Surgery Profile and nested policy blocks.

```bash
uv run streamlit run streamlit_app.py
```

Alternatively, install `requirements.txt` into a Python virtual environment and run
`streamlit run streamlit_app.py`.

## Deploy

For Streamlit Community Cloud, publish this folder in your repository, select
`streamlit_app.py` as the entry point and Python 3.12+, and paste the three secrets
into the app's secrets settings. The host installs `requirements.txt`.

For another host, install `requirements.txt`, provide `.streamlit/secrets.toml` as a
server-side secret file, and use this start command:

```bash
streamlit run streamlit_app.py --server.address=0.0.0.0 --server.port=8501
```

Expose the app through HTTPS with WebSocket support. Use **one application process /
replica**: the PIN throttle and placeholder write lock are process-local. Multiple
replicas need shared authentication throttling and distributed write coordination.
No deployment has been performed by this project setup.

## Tools

Each agent tool is a separate file:

| File | Responsibility |
| --- | --- |
| `nathan/tools/current_date.py` | Fresh Europe/London date |
| `nathan/tools/get_surgery_profile.py` | Exactly one profile tagged SURGERY PROFILE |
| `nathan/tools/list_policies.py` | Paginated policy inventory and metadata |
| `nathan/tools/read_policy.py` | Markdown v2 policy/profile content and truncated-subtree recovery |
| `nathan/tools/create_placeholder.py` | Restricted creation and read-back verification |

`nathan/agent.py` enforces order and write boundaries. `nathan/model.py` handles
Chat Completions and validates JSON with Pydantic; native tool calling and provider
JSON mode are not required. `nathan/notion.py` provides HTTP transport and pagination.
`nathan/auth.py` handles constant-time PIN comparison and throttling.

## Policy behaviour

Every chat request obtains today's London date, then the Surgery Profile, then the
policy inventory. No model or Notion requests run before the PIN gate is passed.
Failed profile retrieval stops the request. Read requests retry transient failures
once; creation requests are never automatically retried.

As explicitly amended by the user, future `Review_Date` establishes approval/currency.
Today/past/missing dates are not Live. Draft/archived/superseded metadata, templates
and placeholders remain excluded. For operational answers, non-Live procedure text
is withheld from the response model. Reviews may assess non-Live content as such.
Ownership and staff responsibilities remain evidence-based model assessments.

Gap requests read all accessible policy documents, including broader coverage and
existing placeholders. Incomplete reads prevent a confirmed gap. Proposals appear
with a checkbox requiring written policy coverage and a **Create placeholder** button.
The button triggers a fresh date/profile/inventory/read sequence, reconfirms the gap,
and refreshes inventory again immediately before creation. Inventory edits invalidate
the proposal. An exact normalised title check supplements semantic duplicate checks.

Creation sends only `Policy`, `Tags = NEW POLICY REQUIRED`, `Review_Date = today`, and
an empty children array. It does not request a template, assign an owner or change
existing records. The app reads back properties and children before claiming success.
An uncertain creation disables further writes in that browser session; inspect Notion
before starting a new session. External editors can still race a creation because
Notion does not provide a transactional uniqueness constraint for policy titles.

Answers cite read policy IDs, sections and supplied `public_url` values. A page without
a public URL is cited by title without fabricating a public link. The model is instructed
to distinguish formal owners, clinical leads and suggested contacts. All policy advice
remains model-generated: code validates source IDs, dates and write boundaries, not
the truth of every sentence or every clinical interpretation.

## Access and data

The shared PIN gives every admitted user the same access, including the ability to
authorise a placeholder. It is not individual authentication or role-based approval.
Five failed attempts trigger a one-minute server-process cooldown across sessions.
Sessions expire after 30 minutes without interaction; a logout or new chat clears
conversation state. PIN access expires on logout; chats are not stored in a database.
The original playbook is retained in `docs/original-playbook.md`; approved amendments
are in the design document and system prompt.

Questions, recent chat context, the Surgery Profile and relevant policy text are sent
to the configured model endpoint. Nathan does not deliberately log their contents.
Avoid patient-identifiable information. There is no external regulatory verification
tool: legal/CQC/NHS compliance must not be asserted from local policy evidence alone.

## Current limits

Attachments, child-page links, embedded databases and unknown blocks are flagged as
unread content; they are not silently treated as missing coverage. A profile containing
such content fails closed. Empty, completely read pages count as empty coverage.
A document above 180,000 characters or a model request above 350,000 characters stops
with a scope message instead of silently truncating evidence. The provider's own
context limit may be smaller. Large collections can therefore exceed the initial
gap-assessment capacity; no vector search index or document batching is included.

## Verify

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

Tests use simulated Notion/model boundaries and Streamlit AppTest, so they do not
write to the surgery database. Live model endpoint/token connectivity must be checked
after configuring real secrets. Dependencies resolved for local validation are recorded
in `uv.lock`.

## Response streaming and tool visibility

Answers stream from the model endpoint using Chat Completions SSE. The interface
extracts only the answer text from the incremental JSON envelope; raw JSON, source
records and internal reasoning are not displayed. Final source validation runs before
a response is saved or a placeholder proposal becomes available. Failed validation
clears the temporary answer and shows the failure. A live status indicator lists the
actual tools called, and each saved answer retains a collapsible Tool use status panel.

Policy selection has a dedicated task prompt rather than the full policy-answering
playbook. Short source aliases map back to retrieved Notion page IDs. The model receives
specific validation feedback when a repair is needed; fenced JSON is accepted. A
staffing response can cite the retrieved Surgery Profile directly. Unknown or
ambiguous source references still fail validation.

Policy and Surgery Profile content now use
`GET /v1/pages/{page_id}/markdown?markdown_version=v2`, with `Notion-Version: 2026-03-11`
for these read requests. The same reader serves both tools. Unknown subtrees are
retrieved through this endpoint and inserted at their original markers when possible.
Unresolved truncation, inaccessible content and unsupported attachments remain flagged
as incomplete; the Surgery Profile still fails closed when incomplete. Recovery is
bounded to 30 levels and 200 requests. Inventory and placeholder operations retain
their existing API version; placeholder empty-body verification still uses block children.
https://developers.notion.com/guides/data-apis/working-with-markdown-content

Both originally reported questions (cold chain procedure and end-of-life lead) were
successfully checked against the configured live Notion and model APIs after these
changes. These checks performed no writes to Notion.

The Markdown migration was verified live against the Surgery Profile and cold-chain policy; both reported complete content. No Notion records were changed.

Each completed answer shows a Streamlit caption inside its Tool use status panel with the `model` name reported by the endpoint for that answer, retained in chat history. If the gateway reports only `auto`, the caption shows `auto`; an underlying model name is not inferred. Missing metadata is shown as “not reported by endpoint”.

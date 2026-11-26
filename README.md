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

Two optional settings override the model endpoint. Leave them out to keep the defaults
(`auto` on the FreeLLMAPI base URL above). `FREELLMAPI_BASE_URL` must be an `https://` URL.
The same two names can be set as environment variables when they are absent from the secrets
file. A blank value in the secrets file keeps the default and does not fall through to the
environment.

```toml
# FREELLMAPI_BASE_URL = "https://freellmapi-4ace.srv1395197.hstgr.cloud/v1"
# FREELLMAPI_MODEL = "auto"
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
| `nathan/tools/list_overdue_policies.py` | Overdue and due-soon list from that metadata only |
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

Questions about which policies are overdue, out of date, expired, past review, due for review,
or due within 30 days are answered by `List Overdue Policies` before any model call. That tool
uses the paginated inventory only: title, owner, review date, status and tags. It does not read
the Surgery Profile or any policy text, and it is not subject to the 15-policy or response-size
limit. Live policies whose review date falls in the next 30 days are listed as due soon.
Wording such as “list all overdue policies” and “show out of date policies” takes this path.
A how-to question that merely mentions the word overdue, such as what to do if a policy is
overdue, still uses the normal read-and-answer path.

Other register questions — how many policies there are, a list of the policies held, or which
are draft or placeholder — are still answered in Python from the same metadata after a readable
Surgery Profile. They do not call the language model. Each of those status groups shows at most
40 titles; the counts are always the full inventory. A question about what a policy says still
uses the normal read-and-answer path.

A single odd Notion record does not discard the register. Missing properties, null tags or
owner names, formula or rollup dates, rich-text dates and people fields are read when the
shape is recognised; a value that still cannot be read is listed as missing or unreadable.
If Notion itself rejects the query, the chat names the HTTP status and the likely permission
or property problem. The underlying response is written to the server log with tokens removed.

Policy selection combines ranked title/tag keyword and synonym matches with semantic model
selection. Common terms such as ECG/electrocardiography, cold chain/vaccine storage,
end-of-life/palliative care, confidentiality, complaints and infection control are expanded
locally. A single shared word from a multi-word topic (for example "chain" in "cold chain")
does not by itself select an unrelated policy. Short follow-ups such as "Who is responsible
for that?" reuse the previous topic when the latest wording has no lexical match of its own.
The combined selection reads at most 15 policies and does not fill empty selections with
unrelated inventory records.

Gap requests read at most 15 relevant policies, including broader coverage and existing
placeholders. Unsupported Confirmed gap assessments are downgraded to Potential gap,
with a scope explanation instead of an error. No title/tag candidate found is distinguished
from relevant content that could not be read. Failed policy reads do not stop the remaining
reads, do not prove missing coverage and cannot support content citations. Incomplete or
capped reads cannot produce an actionable creation proposal.
Proposals display a synopsis and a checkbox requiring written policy coverage, followed
by a **Create placeholder** button. Authorization uses the saved proposal and inventory;
it does not reload the Surgery Profile, list policies, reread policies or call the model.
The creation date is obtained again in Europe/London. Duplicate title checks use the
saved inventory, so edits made in Notion after the proposal are not rechecked.

Creation sends `Policy`, `Tags = NEW POLICY REQUIRED`, `Review_Date = today`,
`Agent Advice` (rich text containing a synopsis of what the policy needs to cover), and
an empty children array. It does not assign an owner or change existing records.
The app reads back all four properties and children before claiming success.
An uncertain creation disables further writes in that browser session; inspect Notion
before starting a new session. External editors can still race a creation because
Notion does not provide a transactional uniqueness constraint for policy titles.

Answers cite read policy IDs, sections and supplied `public_url` values under a **Sources**
list. A page without a public URL is cited by title without fabricating a public link.
A cited section that does not appear in the retrieved text is labelled as not found, and
the model is asked to correct it. An operational answer with no citation is explicitly
marked as not covered by a cited policy section, so silence in the policies is not presented
as a procedure. The model is instructed to distinguish formal owners, clinical leads and
suggested contacts, and not to fill a gap from general knowledge. All policy advice remains
model-generated: code validates source IDs, section wording, dates and write boundaries, not
the truth of every sentence or every clinical interpretation.

## Access and data

The shared PIN gives every admitted user the same access, including the ability to
authorise a placeholder. It is not individual authentication or role-based approval.
Five failed attempts trigger a one-minute server-process cooldown across sessions.
Re-indexing is separate: it needs `ADMIN_PIN` and does not accept the access PIN.
Individual login, such as Streamlit OIDC, is not built.
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
with a scope message instead of silently truncating evidence. A shorter document that is
still above 16,000 characters and has Markdown headings is narrowed to the opening section
and the sections that match the question. The answer says which policy was focused, and
omitted sections are not treated as missing coverage. If no section matches, the full text
is still sent. The provider's own context limit may be smaller. Large collections can
therefore exceed the initial gap-assessment capacity; no vector search index is included.

## How the policy index works

Nathan can search the words inside policies, not only their titles. This is off until Supabase is configured. With nothing set, the sidebar says **Search: keyword mode** and answers work as they do today.

### Chunking

Each policy is split on Markdown headings, so a chunk is one section and is stored with the policy title and that heading. A section under about 40 words is merged into its neighbour. A section over about 500 words is cut into pieces of about 400 words with a 40-word overlap, and each piece keeps the same heading and a chunk number so the next piece can be shown with it. The text sent to the embedding model is the title, the heading, then the words.

### Sync

A background task, about every 10 minutes, compares each Notion page's `last_edited_time` with the index and refreshes at most two changed pages so a chat message does not wait. Unchanged words are recognised by a content hash and are not embedded again. Changing the embedding model name or size marks every row stale. Pages that disappear from Notion, or are archived, are removed. Only one sync runs at a time: a lock row in Postgres, plus an advisory lock when the connection stays open. The first full build is the command in the setup list, or the admin **Re-index policies** button. The button is hidden unless `ADMIN_PIN` is set, and it does not accept the normal access PIN.

### Retrieval

A question is matched three ways, then combined with reciprocal rank fusion (no hand-tuned mix of scores):

1. pgvector similarity
2. Postgres full-text search
3. the existing title and tag match

Ordinary answers use Live chunks only. If a non-live policy is still cited, the source line says **Not Live** and the procedure text is withheld. The answer can use sections from more than 15 policies because it receives chunks, not whole documents. Citations still name the policy and the section heading. If Supabase is unset, or a search fails, Nathan logs that and stays in keyword mode.

## Switch on the policy index

Do these steps in order. Until the last ones are done, Nathan stays in keyword mode and does not error.

1. In Supabase, create a project. Choose the **London** region (or another UK/EU region) so the policy text stays in the UK.
2. Open the project. In the left sidebar open **Database**, then **Extensions**. Search for **vector** and enable it. If you prefer, the migration below also runs `create extension if not exists vector`.
3. Open **SQL Editor**, then **New query**.
4. On your computer, open the file `supabase/migrations/001_policy_chunks.sql` in this project. Select the whole file and copy it.
5. Paste it into the SQL editor and click **Run**. You should see success, and a table named `policy_chunks`. If you already ran this file before the 403 fix, also paste and run `supabase/migrations/002_grant_index_functions.sql` the same way. That second file only grants the secret key permission to call the index functions. It does not delete any rows. Then reboot the Streamlit app.
6. Open **Project Settings** (the gear), then **API** (or **Data API**).
7. Copy the **Project URL**. It looks like `https://abcdef.supabase.co`.
8. On the same screen, under **Project API keys**, copy the **secret** key. It starts with `sb_secret_`. If your project still shows the legacy **service_role** key, that one also works: it is a long value starting with `eyJ`. Do not copy a key starting with `sb_publishable_` and do not copy the **anon** key. The secret key must only live in Streamlit secrets, never in the chat page and never in git.
9. In Streamlit Community Cloud, open the app, then **Settings**, then **Secrets**. Add these lines under the three secrets you already have. Keep the quotes.

```toml
SUPABASE_URL = "https://abcdef.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "paste-the-service-role-secret"
FREELLMAPI_EMBEDDING_MODEL = "text-embedding-3-small"
ADMIN_PIN = "choose-a-long-admin-pin"
```

`FREELLMAPI_EMBEDDING_MODEL` is optional. Leave it out to use `text-embedding-3-small`, which is 1536 numbers per chunk and matches the migration. The embedding call uses the same `FREELLMAPI_SPI_KEY` and base URL as chat. If that endpoint cannot embed, the log says the embedding request failed and does not print the key. Chunks are not stored until an embedding succeeds, and answers keep using the old title search for that question.

10. Restart the Streamlit app so it reads the new secrets. If the two Supabase secrets were read, the sidebar says **Search: policy index**. If they were not, it says **Search: keyword mode**. A failed search switches the label back to keyword mode.
11. Build the first full index from your computer, in the project folder, with the same values in the environment (do not put them in a file that git tracks):

```bash
export NOTION_TOKEN="your-notion-token"
export FREELLMAPI_SPI_KEY="your-api-key"
export ACCESS_PIN="your-pin"
export SUPABASE_URL="https://abcdef.supabase.co"
export SUPABASE_SERVICE_ROLE_KEY="paste-the-service-role-secret"
python -m nathan.index_cli
```

Wait until it prints `Index update finished`. That command can also be run on a schedule later.
12. Confirm it worked: in Supabase open **Table Editor**, then `policy_chunks`, and check there are rows. In the app sidebar, ask a question whose answer is inside a policy rather than in its title. The tool list should include **Search Policy Index**, the source line should say **Indexed sections**, and the sidebar should say **Search: policy index**. If it still says keyword mode, the secrets are missing or the embedding call failed; check the Streamlit logs.

To compare retrieval before and after, run `python -m nathan.retrieval_eval`. The report uses a draft question set labelled as such in `evals/draft_set.py`. Replace it by adding `evals/staff_questions.json`: a list of objects with `question`, `policy_id`, `section`, and `title`. No reranker is included.

A nightly GitHub Action (`nightly index smoke`) calls Notion and Supabase only when those three secrets are stored in GitHub Actions. If they are absent, it prints SKIP and passes. It prints counts, not policy text.

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

Follow-up questions carry a session-local topic and the IDs/titles of previously read
policies. The selection stage resolves short references such as “Who is responsible for
that?” into a standalone question before keyword search and answer generation. Previous
policy IDs are mapped to the current inventory; deleted records are omitted. Explicit
topic changes replace the earlier scope. Recent chat text helps interpret references,
but current policy/profile reads remain the evidence for each answer. New chat, logout
and session expiry clear this context along with the conversation.

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

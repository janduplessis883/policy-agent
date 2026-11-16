"""Run with: streamlit run streamlit_app.py"""

import time
from contextlib import contextmanager

import streamlit as st

from nathan.agent import Agent
from nathan.auth import PinGuard
from nathan.config import Settings
from nathan.domain import SourceError, UncertainWrite
from nathan.model import PolicyModel
from nathan.notion import NotionClient
from nathan.sources import Sources

st.set_page_config(
    page_title="Nathan | Stanhope Mews Surgery", page_icon=":material/policy:", layout="centered"
)
st.title("Nathan")
st.caption("Stanhope Mews Surgery · Internal policy assistant")

try:
    settings = Settings.from_secrets(st.secrets)
except (SourceError, FileNotFoundError):
    st.error(
        "Configure ACCESS_PIN, FREELLMAPI_SPI_KEY and NOTION_TOKEN as quoted strings "
        "in .streamlit/secrets.toml before using Nathan."
    )
    st.stop()


@st.cache_resource(max_entries=1)
def pin_guard():
    return PinGuard()


def clear_session():
    for key in list(st.session_state):
        del st.session_state[key]


if st.session_state.get("authenticated") and (
    time.monotonic() - st.session_state.get("last_active", 0) > 1800
):
    clear_session()

if not st.session_state.get("authenticated"):
    with st.form("access"):
        st.text_input("Access PIN", type="password", key="pin_entry")
        submitted = st.form_submit_button("Unlock", type="primary")
    if submitted:
        entered = st.session_state.pop("pin_entry", "")
        if pin_guard().verify(entered, settings.access_pin):
            st.session_state.authenticated = True
            st.session_state.last_active = time.monotonic()
            st.rerun()
        st.error("Access denied. Check the PIN. After repeated attempts, wait one minute.")
    st.stop()

st.session_state.last_active = time.monotonic()
st.session_state.setdefault("messages", [])
st.session_state.setdefault("pending", None)
st.session_state.setdefault("uncertain_write", False)

with st.sidebar:
    st.subheader("Nathan")
    st.caption("Ask about policies, review a document or check policy coverage.")
    if st.button("New chat", key="new_chat"):
        st.session_state.messages = []
        st.session_state.pending = None
        st.rerun()
    if st.button("Log out", key="logout"):
        clear_session()
        st.rerun()
    st.caption(
        "Access expires after 30 minutes without activity. "
        "Please keep patient-identifiable information out of this chat."
    )


@contextmanager
def agent_session(on_event=None, on_text=None):
    # Clients contain credentials but never leave the server or persist chat data.
    notion = NotionClient(settings.notion_token)
    model = PolicyModel(settings)
    try:
        yield Agent(Sources(notion), model, on_event=on_event, on_text=on_text)
    finally:
        notion.close()
        model.close()


for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message.get("tools"):
            with st.expander("Tools used"):
                for tool in message["tools"]:
                    st.text(tool)

if not st.session_state.messages:
    st.write("How can I help with surgery policies?")
    st.caption(
        "For example: “What is our cold chain procedure?” or "
        "“Review our complaints policy for unclear responsibilities.”"
    )

pending = st.session_state.pending
if pending and not st.session_state.uncertain_write:
    with st.container(border=True):
        st.write("Proposed placeholder: **" + pending["title"] + "**")
        st.caption(
            "Creates one empty Notion record tagged NEW POLICY REQUIRED, "
            "with today’s London date in Review_Date. Coverage is checked again before creation."
        )
        authorised = st.checkbox(
            "I require written policy coverage for this subject and "
            "authorise this empty placeholder.",
            key="authorise_placeholder",
        )
        if st.button("Create placeholder", type="primary", disabled=not authorised):
            # Consume the proposal before external work, preventing a rerun from resubmitting it.
            st.session_state.pending = None
            try:
                with st.status("Checking placeholder coverage…", expanded=True) as status:

                    def report_creation_tool(event):
                        status.update(label=event)
                        status.write(event)

                    with agent_session(on_event=report_creation_tool) as agent:
                        result = agent.create(
                            pending["title"], pending["question"], st.session_state.messages
                        )
                    status.update(label="Placeholder verified", state="complete", expanded=False)
                st.session_state.messages.append({"role": "assistant", "content": result.text})
            except UncertainWrite as exc:
                st.session_state.uncertain_write = True
                st.session_state.messages.append({"role": "assistant", "content": str(exc)})
            except SourceError as exc:
                st.session_state.messages.append({"role": "assistant", "content": str(exc)})
            except Exception:  # noqa: BLE001 -- never expose credentials in unexpected errors
                st.session_state.uncertain_write = True
                st.session_state.messages.append(
                    {
                        "role": "assistant",
                        "content": "Creation could not be verified. Check Notion before attempting another write.",
                    }
                )
            st.rerun()

if st.session_state.uncertain_write:
    st.warning(
        "Placeholder creation is disabled in this session because an earlier write "
        "could not be verified. Check the Notion collection before starting another session."
    )

if question := st.chat_input("Ask Nathan about surgery policies", max_chars=8000):
    history = list(st.session_state.messages)
    st.session_state.pending = None
    st.session_state.pop("authorise_placeholder", None)
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    tools = []
    with st.chat_message("assistant"):
        status = st.status("Checking surgery sources…", expanded=True)
        output = st.empty()

        def report_tool(event):
            tools.append(event)
            status.update(label=event)
            status.write(event)

        def show_answer(text):
            status.update(label="Writing answer…")
            if text:
                output.markdown(text)
            else:
                output.empty()

        try:
            with agent_session(on_event=report_tool, on_text=show_answer) as agent:
                result = agent.ask(question, history)
            output.markdown(result.text)
            status.update(label="Answer complete", state="complete", expanded=False)
            st.session_state.messages.append(
                {"role": "assistant", "content": result.text, "tools": tools}
            )
            if result.proposal and not st.session_state.uncertain_write:
                st.session_state.pending = {"title": result.proposal, "question": question}
        except SourceError as exc:
            output.empty()
            status.update(label="Request could not be verified", state="error", expanded=False)
            st.session_state.messages.append(
                {"role": "assistant", "content": str(exc), "tools": tools}
            )
        except Exception:  # noqa: BLE001 -- never expose credentials in unexpected errors
            output.empty()
            status.update(label="Request failed", state="error", expanded=False)
            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "tools": tools,
                    "content": "The request could not be completed. Please try again or contact the app administrator.",
                }
            )
    st.rerun()

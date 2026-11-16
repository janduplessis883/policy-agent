from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "streamlit_app.py")


def app():
    at = AppTest.from_file(APP)
    at.secrets.update(
        {"ACCESS_PIN": "123456", "FREELLMAPI_SPI_KEY": "test", "NOTION_TOKEN": "test"}
    )
    return at.run()


def test_pin_gate_and_logout_clear_chat():
    at = app()
    assert not at.exception
    assert len(at.chat_input) == 0
    at.text_input[0].input("123456")
    at.button[0].click().run()
    assert not at.exception
    assert len(at.chat_input) == 1
    at.session_state["messages"] = [{"role": "user", "content": "private conversation"}]
    at.button(key="logout").click().run()
    assert not at.exception
    assert len(at.chat_input) == 0
    assert "messages" not in at.session_state


def test_incomplete_secrets_show_setup_message():
    at = AppTest.from_file(APP)
    at.secrets["ACCESS_PIN"] = ""
    at.run()
    assert not at.exception
    assert len(at.chat_input) == 0
    assert len(at.error) == 1


def test_chat_and_explicit_placeholder_authorisation(monkeypatch):
    from nathan.agent import Agent
    from nathan.domain import Result

    created = []
    monkeypatch.setattr(Agent, "ask", lambda *args: Result("Potential gap.", "New policy"))

    def create(self, title, question, history):
        created.append((title, question))
        return Result("Created New policy: page body empty.")

    monkeypatch.setattr(Agent, "create", create)
    at = app()
    at.text_input[0].input("123456")
    at.button[0].click().run()
    at.chat_input[0].set_value("Check coverage for this subject").run()
    assert not at.exception
    assert at.checkbox[0].value is False
    create_button = next(b for b in at.button if b.label == "Create placeholder")
    assert create_button.disabled
    assert created == []
    at.checkbox[0].check().run()
    next(b for b in at.button if b.label == "Create placeholder").click().run()
    assert not at.exception
    assert created == [("New policy", "Check coverage for this subject")]
    assert at.session_state["pending"] is None


def test_streamed_answer_and_tool_indicator_are_wired_to_chat(monkeypatch):
    from nathan.agent import Agent
    from nathan.domain import Result

    def ask(self, question, history):
        self.on_event("Current Date · Europe/London")
        self.on_event("Get Surgery Profile")
        self.on_text("The recorded ")
        self.on_text("The recorded lead is Test person.")
        return Result(
            "The recorded lead is Test person.\n\nProfile · Surgery Profile",
            model="routed-answer-model",
        )

    monkeypatch.setattr(Agent, "ask", ask)
    at = app()
    at.text_input[0].input("123456")
    at.button[0].click().run()
    at.chat_input[0].set_value("Who is the lead?").run()
    assert not at.exception
    assert at.session_state["messages"][-1]["tools"] == [
        "Current Date · Europe/London",
        "Get Surgery Profile",
    ]
    assert any(e.label == "Tool use" for e in at.status)
    status = next(e for e in at.status if e.label == "Tool use")
    assert any(c.value == "Model: routed-answer-model" for c in status.caption)
    assert "Profile · Surgery Profile" in at.session_state["messages"][-1]["content"]

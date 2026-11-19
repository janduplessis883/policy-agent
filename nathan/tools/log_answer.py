from datetime import datetime

from nathan.config import QUESTION_LOG_DATA_SOURCE_ID


def log_answer(client, question, answer, completed_at: datetime, models, total_tokens):
    """Create a single Notion entry containing the final rendered answer."""
    title = [
        {"text": {"content": question[start : start + 2000]}}
        for start in range(0, len(question), 2000)
    ]
    return client.request(
        "POST",
        "pages",
        write=True,
        headers={"Notion-Version": "2026-03-11"},
        json={
            "parent": {"type": "data_source_id", "data_source_id": QUESTION_LOG_DATA_SOURCE_ID},
            "properties": {
                "Question?": {"title": title},
                "Date": {"date": {"start": completed_at.isoformat(timespec="seconds")}},
                "Model": {
                    "rich_text": [{"text": {"content": ", ".join(models)}}] if models else []
                },
                "Tokens used": {"number": total_tokens},
            },
            "markdown": answer,
        },
    )

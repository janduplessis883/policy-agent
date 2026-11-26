"""Draft retrieval questions generated from fixture headings.

These are not questions staff have asked. Replace this module's data by adding
evals/staff_questions.json, which nathan.retrieval_eval prefers when it exists.
"""

# Each section body uses words that are not in the policy title, so title/tag
# search should miss and full-text search should hit. Bodies are long enough
# that chunking does not merge neighbouring sections.


def _body(phrase: str) -> str:
    sentence = phrase.strip().rstrip(".")
    return " ".join([sentence] * 8) + "."


DRAFT_POLICIES = [
    {
        "id": "cold-chain",
        "title": "Cold chain",
        "sections": [
            (
                "Fridge temperatures",
                _body("Record the appliance minimum and maximum twice each working day"),
                "How often is the appliance minimum recorded?",
            ),
            (
                "Vaccine receipt",
                _body("On delivery check the consignment paperwork and quarantine damaged boxes"),
                "What paperwork is checked when a consignment arrives?",
            ),
            (
                "Breakdown",
                _body("Quarantine the stock and tell the duty coordinator"),
                "Who is told to quarantine the stock?",
            ),
            (
                "Data logger",
                _body("Download the temperature logger at the end of every month"),
                "When is the temperature logger downloaded?",
            ),
            (
                "Transport",
                _body("Use a validated cool box for outreach clinics"),
                "What container is used for outreach clinics?",
            ),
            (
                "Exclusions",
                _body("Do not store sandwiches or drinks beside the medicines"),
                "May sandwiches be stored beside the medicines?",
            ),
        ],
    },
    {
        "id": "safeguarding",
        "title": "Safeguarding",
        "sections": [
            (
                "Child concern",
                "Speak to the named lead for children on the same working day.",
                "When must the named lead for children be told?",
            ),
            (
                "Adult concern",
                "Consider consent and mental capacity before sharing an adult concern.",
                "What must be considered before sharing an adult concern?",
            ),
            (
                "Referral",
                "Send the local authority referral form the same day.",
                "Which form is used for a local authority referral?",
            ),
            (
                "Recording",
                "Write the facts, not opinions, in the clinical record.",
                "Where are facts rather than opinions written?",
            ),
            (
                "Allegations",
                "Report an allegation about a member of staff to the LADO.",
                "Who receives an allegation about a member of staff?",
            ),
            (
                "Training",
                "Clinical staff repeat level three training every three years.",
                "How often is level three training repeated?",
            ),
        ],
    },
    {
        "id": "complaints",
        "title": "Complaints",
        "sections": [
            (
                "Acknowledgement",
                "Acknowledge a written concern within three working days.",
                "How quickly is a written concern acknowledged?",
            ),
            (
                "Investigation",
                "A manager who was not involved carries out the investigation.",
                "Who carries out an investigation when they were not involved?",
            ),
            (
                "Response",
                "Send the final written response within the published timescale.",
                "What is sent as the final written response?",
            ),
            (
                "Escalation",
                "Offer the Parliamentary and Health Service Ombudsman if the person remains unhappy.",
                "Who is offered if the person remains unhappy?",
            ),
            (
                "Verbal",
                "Offer to write up something said aloud so it can be tracked.",
                "What is offered so something said aloud can be tracked?",
            ),
            (
                "Learning",
                "Record recurring themes at the monthly practice meeting.",
                "Where are recurring themes recorded?",
            ),
        ],
    },
    {
        "id": "infection-control",
        "title": "Infection control",
        "sections": [
            (
                "Hand hygiene",
                "Be bare below the elbows and use alcohol gel between patients.",
                "What gel is used between patients?",
            ),
            (
                "Sharps",
                "Do not resheath needles. Dispose of them in a yellow sharps bin.",
                "Should needles be resheathed?",
            ),
            (
                "Spill",
                "Use chlorine granules on a blood spill and leave them in place.",
                "What is put on a blood spill?",
            ),
            (
                "PPE",
                "Wear gloves and an apron when contact with body fluid is expected.",
                "When are gloves and an apron worn?",
            ),
            (
                "Outbreak",
                "Inform the UKHSA health protection team of a suspected outbreak.",
                "Who is informed of a suspected outbreak?",
            ),
            (
                "Cleaning",
                "Clinical rooms are cleaned at the end of every session.",
                "When are clinical rooms cleaned?",
            ),
        ],
    },
    {
        "id": "chaperone",
        "title": "Chaperone",
        "sections": [
            (
                "When required",
                "Offer a trained observer before an intimate examination.",
                "When is a trained observer offered?",
            ),
            (
                "Recording",
                "Document the observer's full name in the consultation note.",
                "Whose full name is documented in the consultation note?",
            ),
            (
                "Refusal",
                "If the patient declines, the clinician may postpone the examination.",
                "What may the clinician do if the patient declines?",
            ),
            (
                "Who",
                "The observer is a trained member of staff, not a relative.",
                "Can a relative stay as the observer?",
            ),
            (
                "Privacy",
                "Explain what will happen and offer a private undressing space first.",
                "What undressing space is offered first?",
            ),
            (
                "Students",
                "Take separate consent before a student stays in the room.",
                "What consent is needed before a student stays?",
            ),
        ],
    },
    {
        "id": "information-governance",
        "title": "Information governance",
        "sections": [
            (
                "Email",
                "Use an NHS mail account whenever patient details are sent.",
                "Which email account is used for patient details?",
            ),
            (
                "Smartcard",
                "Do not share smartcard credentials or leave the card in a reader.",
                "May smartcard credentials be shared?",
            ),
            (
                "Screen",
                "Lock the computer screen whenever you leave the desk.",
                "What is locked when you leave the desk?",
            ),
            (
                "Paper",
                "Store paper records in a locked cabinet overnight.",
                "Where are paper records stored overnight?",
            ),
            (
                "Incident",
                "Report a confidentiality breach to the practice manager the same day.",
                "Who is told about a confidentiality breach the same day?",
            ),
            (
                "Retention",
                "Follow the NHS records retention schedule before destroying records.",
                "Which schedule is followed before destroying records?",
            ),
        ],
    },
]

for _policy in DRAFT_POLICIES:
    _policy["sections"] = [
        (
            heading,
            body if len(body.split()) >= 45 else _body(body),
            question,
        )
        for heading, body, question in _policy["sections"]
    ]

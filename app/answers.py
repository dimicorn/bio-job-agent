"""Reusable, user-authored screening answers. Sensitive answers are never inferred."""

import json
import re

import yaml

from app.materials import save_version
from app.private import private_open
from app.resume import require_reviewed


def norm(label):
    return re.sub(r"\s+", " ", label.replace("*", "")).strip().casefold().rstrip(" ?:.")


def load_answers(cfg):
    path = cfg.resolved(cfg.apply.answers_file)
    data = yaml.safe_load(path.read_text()) if path.exists() else {}
    if data is None:
        return {}
    if not isinstance(data, dict) or any(
        not isinstance(k, str)
        or not isinstance(v, (str, list))
        or (isinstance(v, list) and any(not isinstance(x, str) for x in v))
        for k, v in data.items()
    ):
        raise ValueError("answers.yaml must map questions to text or lists of text")
    return {norm(k): v for k, v in data.items()}


def lookup(bank, employer, label):
    return bank.get(norm(f"{employer}: {label}"), bank.get(norm(label)))


def save_answer(cfg, label, value, employer=None):
    if not value or not all(
        isinstance(x, str) and x.strip() for x in (value if isinstance(value, list) else [value])
    ):
        raise ValueError("Answer must contain nonempty text")
    path = cfg.resolved(cfg.apply.answers_file)
    data = yaml.safe_load(path.read_text()) or {} if path.exists() else {}
    load_answers(cfg)  # validate before changing anything
    if employer and any(norm(k) == norm(label) for k in data):
        label = f"{employer}: {label}"
    data = {k: v for k, v in data.items() if norm(k) != norm(label)}
    data[label] = value
    with private_open(path, encoding="utf-8") as output:
        output.write(
            "# User-confirmed screening answers; reused across employers.\n"
            + yaml.safe_dump(data, allow_unicode=True, sort_keys=True)
        )


def add_question(conn, job_id, label, options=None, required=True):
    if not label.strip() or any(not isinstance(x, str) or not x.strip() for x in options or []):
        raise ValueError("Question and options must be nonempty text")
    conn.execute(
        """INSERT INTO screening_questions(job_id,label,options,required) VALUES (?,?,?,?)
        ON CONFLICT(job_id,label) DO UPDATE SET options=excluded.options,required=excluded.required""",
        (job_id, label.strip(), json.dumps(options or []), required),
    )
    conn.commit()


def plan(cfg, conn, row):
    bank = load_answers(cfg)
    items = []
    for question in conn.execute(
        "SELECT * FROM screening_questions WHERE job_id=? ORDER BY id", (row["id"],)
    ):
        options = json.loads(question["options"])
        value = lookup(bank, row["employer"], question["label"])
        values = value if isinstance(value, list) else [value] if value else []
        if not all(v.strip() for v in values):
            values = []
        if options:
            choices = {o.casefold(): o for o in options}
            values = [choices.get(v.casefold(), "") for v in values]
            if not all(values):
                values = []
        items.append(
            {
                "question": question["label"],
                "options": options,
                "required": bool(question["required"]),
                "answer": values,
                "source": "answers.yaml" if values else "needs_human",
            }
        )
    return items


def snapshot(cfg, conn, profile, row):
    require_reviewed(cfg, profile)
    items = plan(cfg, conn, row)
    warnings = [f"Unanswered: {i['question']}" for i in items if i["required"] and not i["answer"]]
    return save_version(
        cfg,
        conn,
        profile,
        row,
        "answers",
        yaml.safe_dump({"items": items}, allow_unicode=True, sort_keys=False),
        warnings,
        {"source": str(cfg.resolved(cfg.apply.answers_file))},
    )


def collect(cfg, conn, profile, rows, *, interactive=False, output=print):
    for row in rows:
        for item in plan(cfg, conn, row):
            if item["answer"]:
                continue
            output(f"{row['id']} {row['employer']}: {item['question']}")
            if not interactive:
                continue
            for i, option in enumerate(item["options"], 1):
                output(f"  {i}. {option}")
            raw = input("Answer (option numbers separated by commas; Enter skips): ").strip()
            if not raw:
                continue
            if item["options"]:
                try:
                    indexes = [int(x.strip()) for x in raw.split(",")]
                    if not indexes or any(i < 1 or i > len(item["options"]) for i in indexes):
                        raise ValueError("Invalid option")
                    value = [item["options"][i - 1] for i in indexes]
                except ValueError:
                    output("Invalid option; left unanswered.")
                    continue
            else:
                value = raw
            save_answer(cfg, item["question"], value, row["employer"])
        snapshot(cfg, conn, profile, row)

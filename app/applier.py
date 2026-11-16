"""Conservative DOM form filling shared by supported ATSs; no evasion or challenge handling."""

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from app import answers

CONFIRM = re.compile(
    r"thank you for (?:applying|your application)|"
    r"(?:your )?application (?:has been |was )?(?:successfully )?(?:submitted|received)|"
    r"we(?:'ve| have) received your application",
    re.IGNORECASE,
)
SENSITIVE = re.compile(
    r"\b(?:visa|sponsors?hip|rights? to work|work authori[sz]ation|legally|legal|"
    r"citizen\w*|nationality|immigration|criminal|convict\w*|felony|"
    r"authori[sz]ed to work|eligib\w* to work|employment eligibility|work permit|"
    r"permission to work|entitled to work|export control|sanctions|non.?compete|"
    r"marital|military|background check|conflict of interest|"
    r"gender|sex|sexual|race|racial|ethnic\w*|disabilit\w*|veteran|"
    r"religion|date of birth|age|pronouns?|accommodations?|demograph\w*|equal opportunit\w*|"
    r"consent|agree|certify|attest|declaration|terms|privacy|gdpr|assessment|"
    r"test task|work sample|coding challenge|take.home assignment|psychometric)\b",
    re.IGNORECASE,
)

# Only add bookkeeping attributes. Never alter required/validation or application behavior.
FIELDS_JS = r"""() => {
 const text = e => (e?.innerText || e?.textContent || '').trim();
 const labelText = e => {
   if (!e) return '';
   const copy = e.cloneNode(true);
   copy.querySelectorAll('input,textarea,select,button,[role=option]').forEach(n => n.remove());
   return text(copy);
 };
 const labelled = e => (e.getAttribute('aria-labelledby') || '').split(/\s+/)
   .map(id => text(document.getElementById(id))).filter(Boolean).join(' ');
 const label = e => labelled(e) || e.getAttribute('aria-label') ||
   [...(e.labels || [])].map(labelText).join(' ') || labelText(e.closest('label'));
 const visible = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
 const seen = new Set(), out = [];
 const elements = [...document.querySelectorAll(
   'input,textarea,select,[role=combobox],[role=radio],[role=checkbox],button[data-option],[aria-required=true],[contenteditable=true]')];
 for (const e of elements) {
   if (!e.matches('input,textarea,select,[role=combobox],[role=radio],[role=checkbox],button[data-option]') &&
       e.querySelector('input,textarea,select,[role=combobox],[role=radio],[role=checkbox],button[data-option]')) continue;
   const type = (e.type || e.getAttribute('role') || e.tagName).toLowerCase();
   if (['hidden','submit','button','reset'].includes(type) && !e.hasAttribute('data-option')) continue;
   if (!visible(e) && !((type === 'file' || e.required || e.getAttribute('aria-required') === 'true') && visible(e.parentElement)) &&
       !((type === 'radio' || type === 'checkbox') && visible(e.closest('label')))) continue;
   const box = e.closest('[data-field-path],fieldset,[role=group],[role=radiogroup],.application-question,.field');
   const group = ['radio','checkbox'].includes(type) || e.hasAttribute('data-option');
   const key = group ? (box || e.parentElement) : e;
   if (seen.has(key)) continue;
   seen.add(key);
   let options = [];
   let controls = [e];
   let question = label(e);
   if (group) {
     controls = [...key.querySelectorAll('input[type=radio],input[type=checkbox],[role=radio],[role=checkbox],button[data-option]')];
     question = text(key.querySelector('legend')) || labelled(key) ||
       text(key.querySelector('.application-label,.question-label')) ||
       (controls.length === 1 ? label(e) : text(key.querySelector('label')));
     options = controls.map(c => label(c) || text(c) || c.value || c.getAttribute('data-option'));
   } else if (e.tagName === 'SELECT') {
     options = [...e.options].filter(o => o.value && !o.disabled).map(o => o.text.trim());
   }
   if (!question && box) question = labelText(box.querySelector('label'));
   question ||= e.name || e.id || '';
   const token = key.getAttribute('data-bio-field') || 'bio-' + crypto.randomUUID();
   key.setAttribute('data-bio-field', token);
   const required = controls.some(c => c.required || c.getAttribute('aria-required') === 'true') ||
     key.getAttribute('aria-required') === 'true' || /\*/.test(question) ||
     !!box?.querySelector('[aria-required=true],.required');
   const has_value = group ? controls.some(c => c.checked === true ||
       c.getAttribute('aria-checked') === 'true' || c.getAttribute('aria-pressed') === 'true') :
     type === 'file' ? !!e.files?.length : !!String(e.value || '').trim();
   out.push({token, label: question, options, required, has_value, disabled: controls.some(c => c.disabled),
     kind: group ? 'choices' : type === 'file' ? 'file' :
       e.tagName === 'SELECT' ? 'select' : e.getAttribute('role') === 'combobox' ? 'combo' : 'text',
     identity: [e.name,e.id,e.getAttribute('data-ui'),box?.getAttribute('data-field-path')].filter(Boolean).join(' ')});
 }
 return out;
}"""


class FormStop(RuntimeError):
    pass


@dataclass
class Step:
    field: dict
    values: list[str]
    source: str
    file_hash: str | None = None


class FormApplier:
    SUBMIT = "button[type=submit],input[type=submit]"

    def __init__(self, page, cfg, event=lambda *a, **k: None):
        self.page, self.scope, self.cfg, self.event = page, page, cfg, event

    def open(self, url):
        self.event("open", url=url)
        self.page.goto(self.form_url(url), wait_until="domcontentloaded", timeout=60000)
        self.page.set_default_timeout(5000)
        self.check_safety()
        # Public Greenhouse career pages can contain the actual form in an iframe.
        for frame in self.page.frames:
            if frame != self.page.main_frame and "greenhouse.io" in frame.url:
                self.scope = frame
                break
        self.scope.locator("input,textarea,[role=combobox],[data-field-path]").first.wait_for(
            state="attached", timeout=20000
        )
        self.check_safety()

    def form_url(self, url):
        return url

    def check_safety(self):
        for frame in self.page.frames:
            for selector, reason in (
                ("input[type=password]", "Sign-in required"),
                (
                    (
                        "input[autocomplete=one-time-code],input[id^=security-input],input[id*=security-code],"
                        "input[id*=security_code],input[name*=otp i],input[name*=verification_code i]"
                    ),
                    "MFA/security code required",
                ),
                (
                    (
                        "iframe[src*=captcha i],iframe[title*=captcha i],.g-recaptcha,.h-captcha,"
                        "[data-sitekey],[id*=captcha i]"
                    ),
                    "CAPTCHA present",
                ),
            ):
                nodes = frame.locator(selector)
                if (reason == "CAPTCHA present" and nodes.count()) or any(
                    nodes.nth(i).is_visible() for i in range(nodes.count())
                ):
                    raise FormStop(reason)
            form_texts = frame.locator("form,[role=form]").all_inner_texts()
            for text in form_texts:
                if re.search(
                    r"\b(?:by (?:submitting|clicking)[\s\S]{0,200}(?:agree|consent|certify|acknowledge)|"
                    r"I (?:agree|certify|attest|declare))\b",
                    text,
                    re.IGNORECASE,
                ):
                    raise FormStop("Legal declaration required before submission")
            headings = frame.locator("h1,h2,h3,[role=dialog]")
            for text in headings.all_inner_texts():
                if re.search(
                    r"\b(sign in|log in|two.factor|verify your identity|"
                    r"authentication|assessment|complete .{0,20}test|create an? account|"
                    r"verify you are human|verification code|one.time code|check your email)\b",
                    text,
                    re.IGNORECASE,
                ):
                    raise FormStop("Sign-in, verification or assessment required")

    def fields(self):
        self.check_safety()
        return self.scope.evaluate(FIELDS_JS)

    def node(self, field):
        return self.scope.locator(f'[data-bio-field="{field["token"]}"]')

    def plan(self, fields, values, bank, employer, queue):
        self.queue = queue
        steps, stops = [], []
        for field in fields:
            label = field["label"]
            if SENSITIVE.search(label + " " + field["identity"]):
                if not answers.lookup(bank, employer, label):
                    queue(label, field["options"], field["required"])
                stops.append(f"Human declaration/assessment required: {label}")
                continue
            if field["disabled"]:
                if field["required"]:
                    stops.append(f"Required field disabled: {label}")
                continue
            key = standard_key(field)
            value = (
                values.get("letter_file" if key == "letter" and field["kind"] == "file" else key)
                if key
                else None
            )
            source = key if value else "answers.yaml (approved snapshot)"
            if not value:
                value = answers.lookup(bank, employer, label)
            if value:
                wanted = value if isinstance(value, list) else [value]
                if field["options"]:
                    choices = {answers.norm(o): o for o in field["options"]}
                    wanted = [choices.get(answers.norm(v), "") for v in wanted]
                if all(isinstance(v, str) and v.strip() for v in wanted):
                    file_hash = (
                        hashlib.sha256(Path(wanted[0]).read_bytes()).hexdigest()
                        if field["kind"] == "file"
                        else None
                    )
                    steps.append(Step(field, wanted, source, file_hash))
                    continue
            # Optional questions are queued too, and left blank. No inferred responses.
            queue(label or "Unlabelled field", field["options"], field["required"])
            if field["required"]:
                stops.append(f"Unknown required question: {label}")
        if stops:
            raise FormStop("; ".join(stops))
        if not steps:
            raise FormStop("No supported application fields found")
        return steps

    def fill_one(self, step):
        f, values = step.field, step.values
        node = self.node(f)
        if f["kind"] == "file":
            node.set_input_files(values[0])
        elif f["kind"] == "select":
            node.select_option(label=values)
        elif f["kind"] == "choices":
            controls = node.locator(
                "input[type=radio],input[type=checkbox],"
                "[role=radio],[role=checkbox],button[data-option]"
            )
            wanted = {answers.norm(v) for v in values}
            for i, option in enumerate(f["options"]):
                control = controls.nth(i)
                selected = answers.norm(option) in wanted
                if control.evaluate("e => e.matches('input')"):
                    if selected or control.get_attribute("type") == "checkbox":
                        control.set_checked(selected)
                else:
                    checked = control.get_attribute("aria-checked") or control.get_attribute(
                        "aria-pressed"
                    )
                    if (checked == "true") != selected:
                        control.click()
        elif f["kind"] == "combo":
            if len(values) != 1:
                raise FormStop(f"Unsupported multi-combobox: {f['label']}")
            node.click()
            if node.evaluate("e => e.matches('input')"):
                node.fill(values[0])
            option = self.scope.locator("[role=option],.select__option").filter(
                has_text=re.compile(r"^\s*" + re.escape(values[0]) + r"\s*$", re.IGNORECASE)
            )
            option.first.wait_for(state="visible")
            if option.count() != 1:
                raise FormStop(f"Ambiguous option: {f['label']}")
            option.click()
        else:
            if len(values) != 1:
                raise FormStop(f"Multiple answers for text field: {f['label']}")
            node.fill(values[0])
        self.page.wait_for_timeout(self.cfg.apply.action_seconds * 1000)
        self.event("fill", field=f["label"], source=step.source)
        self.verify_one(step)

    def verify_one(self, step):
        f, wanted = step.field, step.values
        node = self.node(f)
        if node.count() != 1:
            raise FormStop(f"Field disappeared or became ambiguous: {f['label']}")
        kind = f["kind"]
        if kind == "file":
            hashes = node.evaluate("""async e => Promise.all([...e.files].map(async f => {
                const bytes = await crypto.subtle.digest('SHA-256', await f.arrayBuffer());
                return [...new Uint8Array(bytes)].map(b => b.toString(16).padStart(2,'0')).join('');
            }))""")
            if hashes != [step.file_hash]:
                raise FormStop(f"File content verification mismatch: {f['label']}")
            got = node.evaluate("e => [...e.files].map(f => f.name)")
            wanted = [Path(v).name for v in wanted]
        elif kind == "select":
            got = node.evaluate("e => [...e.selectedOptions].map(o => o.text.trim())")
        elif kind == "choices":
            selected = node.locator(
                "input[type=radio],input[type=checkbox],"
                "[role=radio],[role=checkbox],button[data-option]"
            ).evaluate_all(
                "els => els.map(e => e.checked === true || e.getAttribute('aria-checked') === 'true' || e.getAttribute('aria-pressed') === 'true')"
            )
            got = [v for v, yes in zip(f["options"], selected, strict=True) if yes]
        elif kind == "combo":
            # React-select stores selection in rendered value nodes; typed search is not selection.
            got = node.evaluate(r"""e => {
              const root = e.closest('.select__control,[data-field-path],[data-ui]') || e.parentElement;
              const selected = [...root.querySelectorAll('[class*=singleValue],[class*=multiValue__label],[data-selected-value]')];
              if (selected.length) return selected.map(x => (x.innerText || x.textContent).trim());
              const option = document.getElementById(e.getAttribute('aria-activedescendant'));
              if (option?.getAttribute('aria-selected') === 'true') return [option.innerText.trim()];
              return e.matches('input') && e.getAttribute('aria-expanded') !== 'true' && e.value ? [e.value] : [];
            }""")
        else:
            got = [node.input_value()]
        if sorted(" ".join(v.split()) for v in got) != sorted(" ".join(v.split()) for v in wanted):
            raise FormStop(f"Verification mismatch: {f['label']}")
        self.event(
            "verify",
            field=f["label"],
            matched=True,
            expected_hash=hashlib.sha256(json.dumps(step.values).encode()).hexdigest(),
            file_hash=step.file_hash,
        )

    def fill(self, steps):
        # Upload first; asynchronous resume parsing must not overwrite verified contacts.
        for step in sorted(steps, key=lambda s: s.field["kind"] != "file"):
            self.check_safety()
            self.fill_one(step)
        self.verify(steps)

    def verify(self, steps):
        self.check_safety()
        fields = self.fields()
        planned = {s.field["token"] for s in steps}
        for field in fields:
            if field["token"] not in planned and hasattr(self, "queue"):
                self.queue(field["label"], field["options"], field["required"])
            if SENSITIVE.search(field["label"] + " " + field["identity"]):
                raise FormStop(f"Human declaration required: {field['label']}")
            if field["token"] not in planned:
                if field["required"]:
                    raise FormStop(f"New required question: {field['label']}")
                if field["has_value"]:
                    raise FormStop(f"Unapproved value in optional question: {field['label']}")
        for step in steps:
            self.verify_one(step)
        invalid = self.scope.locator("input:invalid,select:invalid,textarea:invalid")
        if any(invalid.nth(i).is_visible() for i in range(invalid.count())):
            raise FormStop("Form validation is incomplete")

    def submit_button(self):
        nodes = self.scope.locator(self.SUBMIT)
        visible = [nodes.nth(i) for i in range(nodes.count()) if nodes.nth(i).is_visible()]
        if len(visible) != 1 or not visible[0].is_enabled():
            raise FormStop("Submit button missing, disabled or ambiguous")
        return visible[0]

    def confirmed(self):
        # URLs alone and clicks are not evidence. Require visible acknowledgement after handoff.
        for frame in self.page.frames:
            if CONFIRM.search(frame.locator("body").inner_text(timeout=2000)):
                return {
                    "url": frame.url,
                    "evidence": CONFIRM.search(frame.locator("body").inner_text()).group(0),
                }
        return None

    def wait_confirmation(self, seconds):
        deadline = time.monotonic() + seconds
        while True:
            self.check_safety()
            proof = self.confirmed()
            if proof:
                return proof
            if time.monotonic() >= deadline:
                return None
            self.page.wait_for_timeout(min(250, max(1, (deadline - time.monotonic()) * 1000)))


def standard_key(field):
    label = answers.norm(field["label"])
    identity = re.sub("[^a-z]", "", field["identity"].casefold())
    aliases = {
        "first_name": ("first name", "given name", "firstname"),
        "last_name": ("last name", "family name", "surname", "lastname"),
        "full_name": ("full name", "name", "your name"),
        "email": ("email", "email address", "e-mail"),
        "phone": ("phone", "phone number", "telephone", "mobile phone"),
        "resume": ("resume", "cv", "resume/cv", "attach resume", "upload resume"),
        "letter": ("cover letter", "coverletter"),
    }
    for key, names in aliases.items():
        if label in names or identity in {re.sub("[^a-z]", "", n) for n in names}:
            return key
    return None


class GreenhouseApplier(FormApplier):
    pass


class LeverApplier(FormApplier):
    SUBMIT = "#btn-submit"

    def form_url(self, url):
        return append_path(url, "apply")


class WorkableApplier(FormApplier):
    SUBMIT = "[data-ui=apply-button]"

    def form_url(self, url):
        return append_path(url, "apply")


class AshbyApplier(FormApplier):
    SUBMIT = 'button:has-text("Submit Application")'

    def form_url(self, url):
        return append_path(url, "application")


def append_path(url, suffix):
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    if not path.endswith("/" + suffix):
        path += "/" + suffix
    return urlunsplit(parts._replace(path=path))


APPLIERS = {
    "greenhouse": GreenhouseApplier,
    "lever": LeverApplier,
    "workable": WorkableApplier,
    "ashby": AshbyApplier,
}


def applier_for(url):
    host = urlsplit(url).hostname or ""
    for domain, ats in (
        ("greenhouse.io", "greenhouse"),
        ("lever.co", "lever"),
        ("workable.com", "workable"),
        ("ashbyhq.com", "ashby"),
    ):
        if host == domain or host.endswith("." + domain):
            return APPLIERS[ats]
    raise FormStop("Unsupported ATS; use manual application")

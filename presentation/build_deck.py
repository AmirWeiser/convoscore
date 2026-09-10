"""Generates presentation/ConvoScore-Presentation.pptx.

Build-time tooling only (python-pptx) - not an application runtime
dependency, not referenced anywhere under app/. Run with:
    .venv/Scripts/python.exe presentation/build_deck.py

Editing: this file *is* the editable source for the deck - change the
SLIDES list / diagram functions below and re-run to regenerate the .pptx.
"""

from pathlib import Path

from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR
from pptx.oxml.ns import qn

HERE = Path(__file__).parent
SCREENSHOTS = HERE / "screenshots"
OUT = HERE / "ConvoScore-Presentation.pptx"

# --- palette -----------------------------------------------------------
NAVY = RGBColor(0x16, 0x2A, 0x3E)
SLATE = RGBColor(0x3D, 0x53, 0x66)
TEAL = RGBColor(0x1F, 0x8A, 0x8A)
AMBER = RGBColor(0xC7, 0x7B, 0x1E)
LIGHT_BG = RGBColor(0xF6, 0xF7, 0xF9)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
INK = RGBColor(0x20, 0x24, 0x28)
MUTED = RGBColor(0x63, 0x6E, 0x77)

FONT = "Calibri"

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
BLANK = prs.slide_layouts[6]


def add_slide():
    return prs.slides.add_slide(BLANK)


def set_background(slide, color=LIGHT_BG):
    bg = slide.background
    bg.fill.solid()
    bg.fill.fore_color.rgb = color


def add_notes(slide, text):
    slide.notes_slide.notes_text_frame.text = text


def add_kicker_title(slide, kicker, title, color=NAVY):
    k = slide.shapes.add_textbox(Inches(0.6), Inches(0.35), Inches(12.1), Inches(0.4))
    tf = k.text_frame
    tf.text = kicker.upper()
    p = tf.paragraphs[0]
    p.font.size = Pt(13)
    p.font.bold = True
    p.font.color.rgb = TEAL
    p.font.name = FONT
    p.font.name = FONT
    for run in p.runs:
        run.font.name = FONT

    t = slide.shapes.add_textbox(Inches(0.55), Inches(0.68), Inches(12.2), Inches(0.9))
    tf = t.text_frame
    tf.word_wrap = True
    tf.text = title
    p = tf.paragraphs[0]
    p.font.size = Pt(30)
    p.font.bold = True
    p.font.color.rgb = color
    p.font.name = FONT
    for run in p.runs:
        run.font.name = FONT
    line = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.6), Inches(1.5), Inches(2.0), Pt(3))
    line.fill.solid()
    line.fill.fore_color.rgb = TEAL
    line.line.fill.background()
    return t


def add_bullets(slide, bullets, left=0.6, top=1.8, width=7.0, height=5.2, size=17, color=INK,
                 bullet_color=TEAL):
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    tf = box.text_frame
    tf.word_wrap = True
    for i, item in enumerate(bullets):
        level = 0
        text = item
        if isinstance(item, tuple):
            text, level = item
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = ("\u2022  " if level == 0 else "\u2013  ") + text
        p.font.size = Pt(size if level == 0 else size - 2)
        p.font.color.rgb = color if level == 0 else MUTED
        p.font.name = FONT
        p.space_after = Pt(10 if level == 0 else 4)
        p.level = 0
        for run in p.runs:
            run.font.name = FONT
    return box


def add_picture_fit(slide, path, left, top, max_w, max_h, border=True):
    from PIL import Image
    with Image.open(path) as im:
        iw, ih = im.size
    ratio = min(Inches(max_w) / iw, Inches(max_h) / ih)
    w, h = Emu(int(iw * ratio)), Emu(int(ih * ratio))
    l = Inches(left) + (Inches(max_w) - w) / 2
    t = Inches(top) + (Inches(max_h) - h) / 2
    pic = slide.shapes.add_picture(str(path), l, t, width=w, height=h)
    if border:
        pic.line.color.rgb = RGBColor(0xD8, 0xDC, 0xE0)
        pic.line.width = Pt(1)
    return pic


def footer(slide, text):
    box = slide.shapes.add_textbox(Inches(0.6), Inches(7.1), Inches(10), Inches(0.3))
    p = box.text_frame.paragraphs[0]
    p.text = text
    p.font.size = Pt(10)
    p.font.color.rgb = MUTED
    p.font.name = FONT
    for run in p.runs:
        run.font.name = FONT


def box(slide, text, left, top, width, height, fill=TEAL, text_color=WHITE, size=13, subtitle=None):
    shp = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(left), Inches(top), Inches(width), Inches(height))
    shp.fill.solid()
    shp.fill.fore_color.rgb = fill
    shp.line.color.rgb = fill
    shp.shadow.inherit = False
    tf = shp.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.text = text
    p.alignment = PP_ALIGN.CENTER
    p.font.size = Pt(size)
    p.font.bold = True
    p.font.color.rgb = text_color
    p.font.name = FONT
    for run in p.runs:
        run.font.name = FONT
    if subtitle:
        p2 = tf.add_paragraph()
        p2.text = subtitle
        p2.alignment = PP_ALIGN.CENTER
        p2.font.size = Pt(size - 4)
        p2.font.color.rgb = text_color
        p2.font.name = FONT
        for run in p2.runs:
            run.font.name = FONT
    return shp


def arrow(slide, x1, y1, x2, y2, color=SLATE, width=2.0, dashed=False, label=None):
    conn = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    conn.line.color.rgb = color
    conn.line.width = Pt(width)
    if dashed:
        ln = conn.line._get_or_add_ln()
        d = ln.makeelement(qn("a:prstDash"), {"val": "dash"})
        ln.append(d)
    conn.line.end_arrowhead = True if False else None  # arrowheads set via XML below
    ln = conn.line._get_or_add_ln()
    tail = ln.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"})
    ln.append(tail)
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        lb = slide.shapes.add_textbox(Inches(mx - 1.1), Inches(my - 0.32), Inches(2.2), Inches(0.32))
        tf = lb.text_frame
        tf.word_wrap = False
        tf.margin_left = 0
        tf.margin_right = 0
        p = tf.paragraphs[0]
        p.text = label
        p.alignment = PP_ALIGN.CENTER
        p.font.size = Pt(10.5)
        p.font.color.rgb = MUTED
        p.font.name = FONT
        for run in p.runs:
            run.font.name = FONT
    return conn


# ------------------------------------------------------------------ #
# Slide 1 - Title
# ------------------------------------------------------------------ #
s = add_slide()
set_background(s, NAVY)
t = s.shapes.add_textbox(Inches(0.9), Inches(2.55), Inches(11.5), Inches(1.3))
p = t.text_frame.paragraphs[0]
p.text = "ConvoScore"
p.font.size = Pt(54)
p.font.bold = True
p.font.color.rgb = WHITE
p.font.name = FONT
for run in p.runs:
    run.font.name = FONT

t2 = s.shapes.add_textbox(Inches(0.95), Inches(3.55), Inches(11.0), Inches(1.0))
p2 = t2.text_frame.paragraphs[0]
p2.text = "Turning raw support conversations into a scored, reviewable, on-call-ready pipeline."
p2.font.size = Pt(20)
p2.font.color.rgb = RGBColor(0xB9, 0xC7, 0xD3)
p2.font.name = FONT
for run in p2.runs:
    run.font.name = FONT
line = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.6), Inches(2.35), Pt(4), Inches(0.95))
line.fill.solid()
line.fill.fore_color.rgb = TEAL
line.line.fill.background()

t3 = s.shapes.add_textbox(Inches(0.95), Inches(6.6), Inches(10), Inches(0.5))
p3 = t3.text_frame.paragraphs[0]
p3.text = "Amir Weiser  ·  Solution Engineer / DevOps take-home"
p3.font.size = Pt(13)
p3.font.color.rgb = RGBColor(0x8A, 0x9A, 0xA8)
p3.font.name = FONT
for run in p3.runs:
    run.font.name = FONT
add_notes(s, "One line: this is a small service that scores support conversations with an "
             "LLM and makes the results reviewable and observable - built to show how I'd "
             "design, deploy, and operate a real system, not just make a demo run once.")

# ------------------------------------------------------------------ #
# Slide 2 - Assignment requirements and constraints
# ------------------------------------------------------------------ #
s = add_slide(); set_background(s)
add_kicker_title(s, "Assignment", "Requirements and constraints")
add_bullets(s, [
    "Ingest support-conversation text two ways: an API call, or a file dropped into storage",
    "Score each conversation with an LLM: sentiment, a risk score, a short rationale",
    "Store results durably and make them reviewable by a person - no separate frontend framework",
    "Package as containers, deploy to Kubernetes, provision cloud resources as code",
    "Show real observability: logs, metrics, a dashboard, an induced failure that's visible end to end",
], top=1.8, width=8.0)
add_bullets(s, [
    ("Hard constraints I held myself to:", 0),
    ("SQS is at-least-once - never claim exactly-once processing anywhere", 1),
    ("An LLM call and a database commit can't be made atomic - don't pretend otherwise", 1),
    ("Keep the take-home right-sized - no CI, no service mesh, no ORM, no new database", 1),
], left=0.6, top=5.0, width=8.0, size=15)
footer(s, "ConvoScore  ·  Assignment scope")
add_notes(s, "This slide exists so the reviewer sees I read the brief as a set of constraints, "
             "not just a feature list - especially the two 'don't oversell it' rules about SQS "
             "and LLM-call atomicity, which shape a lot of the design choices on later slides.")

# ------------------------------------------------------------------ #
# Slide 3 - Architecture diagram
# ------------------------------------------------------------------ #
s = add_slide(); set_background(s)
add_kicker_title(s, "Architecture", "Both ingestion paths, one shared pipeline")

box(s, "API\nPOST /conversations", 0.7, 2.0, 2.0, 1.0, fill=TEAL)
box(s, "Direct upload\n(no API call)", 0.7, 3.3, 2.0, 1.0, fill=AMBER)
box(s, "S3\nincoming/{id}.json", 3.3, 2.65, 1.9, 1.0, fill=SLATE)
box(s, "SQS\nprocessing queue", 5.75, 2.65, 1.9, 1.0, fill=SLATE)
box(s, "Worker", 8.2, 2.65, 1.6, 1.0, fill=TEAL)
box(s, "OpenAI", 8.2, 4.1, 1.6, 0.85, fill=NAVY)
box(s, "PostgreSQL", 10.35, 2.65, 1.6, 1.0, fill=NAVY)
box(s, "Review UI", 10.35, 4.1, 1.6, 0.85, fill=SLATE)
box(s, "SQS DLQ", 5.75, 4.4, 1.9, 0.85, fill=RGBColor(0x8B, 0x2E, 0x2E))

arrow(s, 2.7, 2.5, 3.3, 3.05)
arrow(s, 2.7, 3.8, 3.3, 3.25)
arrow(s, 5.2, 3.15, 5.75, 3.15, label="S3 event")
arrow(s, 7.65, 3.15, 8.2, 3.15, label="poll")
arrow(s, 9.0, 3.65, 9.0, 4.1, label="score")
arrow(s, 9.8, 3.15, 10.35, 3.15, label="store")
arrow(s, 11.15, 3.65, 11.15, 4.1)
arrow(s, 6.7, 3.65, 6.7, 4.4, color=RGBColor(0x8B, 0x2E, 0x2E), dashed=True, label="after retries")

add_bullets(s, [
    "The API never calls OpenAI - it validates, writes to Postgres as 'pending', puts the object in S3, returns 202",
    "A direct upload skips the API entirely - the same S3 -> SQS -> worker path picks it up",
    "One validation contract, one claim/scoring path, one place the DLQ can be reached from",
], left=0.6, top=5.6, width=12.0, size=14.5)
footer(s, "ConvoScore  ·  Architecture")
add_notes(s, "The point of this diagram is 'one pipeline, two doors in.' Whether a conversation "
             "arrives via the API or lands in S3 directly, it converges on the exact same S3 -> "
             "SQS -> worker code path - there's no special-casing based on how it arrived. "
             "That's what makes the whole thing testable with one set of behaviors instead of two.")

# ------------------------------------------------------------------ #
# Slide 4 - API ingestion flow
# ------------------------------------------------------------------ #
s = add_slide(); set_background(s)
add_kicker_title(s, "Ingestion path 1", "API ingestion, step by step")
add_bullets(s, [
    "Client sends POST /conversations with {\"text\": \"...\"}",
    "Request body is validated once, through the same Pydantic model the worker also uses "
    "(rejects missing/empty/whitespace-only/non-string/over-20,000-character text)",
    "A 'pending' row is inserted in Postgres first - the id exists before anything touches S3",
    "The conversation is written to S3 under incoming/{id}.json",
    "API returns 202 with the id and a status URL - it does not wait for scoring",
    "If S3 write fails: a bounded HEAD check decides the outcome - object actually landed -> "
    "still 202; definitely never landed -> a visible failed/enqueue_failed row, never a silent "
    "delete of evidence; genuinely unknown -> the row stays visible and pending, client gets a "
    "retryable 503",
], size=16.5)
footer(s, "ConvoScore  ·  API ingestion")
add_notes(s, "The subtle part is the last bullet. The original naive version deleted the "
             "pending row on any S3 exception, assuming the exception proved nothing was "
             "written - but a timeout can mean S3 actually accepted the object and the client "
             "just never saw the response. Deleting the row in that case would erase a "
             "conversation the worker is about to legitimately process. So we check before "
             "deciding, and we never silently destroy evidence when the true outcome is unknown.")

# ------------------------------------------------------------------ #
# Slide 5 - Direct S3 ingestion flow
# ------------------------------------------------------------------ #
s = add_slide(); set_background(s)
add_kicker_title(s, "Ingestion path 2", "Direct S3 ingestion, step by step")
add_bullets(s, [
    "Anything else (a script, a colleague, a batch job) puts a JSON object under incoming/ in the S3 bucket",
    "The bucket has a notification wired to the SQS processing queue for that prefix",
    "S3 emits an ObjectCreated event -> SQS delivers it to the worker - no API involved at all",
    "The worker looks up the row by source_key (the S3 key) - none exists yet, so it creates one",
    "From here it's the identical code path as the API case: validate, claim, score, store",
    "Same S3 key uploaded twice -> the UNIQUE constraint on source_key means the second upload "
    "finds the existing row instead of creating a duplicate",
], size=17)
footer(s, "ConvoScore  ·  Direct S3 ingestion")
add_notes(s, "This is the 'triggers scoring' answer for this path: an S3 ObjectCreated event "
             "under the incoming/ prefix is the trigger, full stop - nothing polls a folder, "
             "nothing depends on the API being up. That's also why source_key (the S3 object "
             "key) had to become the idempotency anchor rather than a request-generated id - a "
             "direct upload never gets one of those from the API.")

# ------------------------------------------------------------------ #
# Slide 6 - Worker, SQS, retries, DLQ, idempotency, claim fencing
# ------------------------------------------------------------------ #
s = add_slide(); set_background(s)
add_kicker_title(s, "Worker internals", "Visibility, retries, DLQ, and claim fencing")
add_bullets(s, [
    "SQS visibility timeout (60s) hides a message while it's being worked - if the worker "
    "doesn't delete it in time, SQS makes it visible again automatically",
    "maxReceiveCount = 3: after 3 delivery attempts without success, SQS moves the message to "
    "a dead-letter queue on its own - the worker never has to implement that part",
    "A transient scoring failure is logged and left undeleted every attempt, not just the last "
    "one - on the final attempt the row is marked failed/transient_exhausted and still left "
    "undeleted, so SQS's own redrive (not our code) is what reaches the DLQ",
    "Idempotency: source_key is UNIQUE - a duplicate delivery of the same object finds the same row",
    "Claim fencing: every claim gets a fresh processing_token; a superseded (older, slower) "
    "attempt can never overwrite what a newer attempt already wrote - it's rejected at the database level",
    "Remaining, honestly stated: a crash between 'OpenAI answered' and 'the guarded commit "
    "landed' can still cause one extra paid OpenAI call - SQS is at-least-once and an LLM call "
    "can't be made atomic with a DB commit",
], size=15)
footer(s, "ConvoScore  ·  Worker, SQS, idempotency")
add_notes(s, "Claim fencing is the one piece of this system I'd call genuinely load-bearing: "
             "without a per-claim token, an old worker attempt that's just running slow could "
             "come back after a newer attempt already reclaimed and finished the same row, and "
             "silently overwrite a correct result with a stale one. The fencing turns that into "
             "'the database rejects the stale write' instead of 'whoever writes last wins.'")

# ------------------------------------------------------------------ #
# Slide 7 - OpenAI integration
# ------------------------------------------------------------------ #
s = add_slide(); set_background(s)
add_kicker_title(s, "Scoring", "OpenAI integration")
add_bullets(s, [
    "Structured output: the model returns sentiment, a 0.0-1.0 risk score, and a one-sentence "
    "rationale - validated against a Pydantic schema, not parsed out of free text",
    "20s request timeout; tenacity-driven retries with exponential backoff (one retry "
    "mechanism, not two - the SDK's own built-in retries are explicitly disabled)",
    "Every result captures the model name, prompt version, input/output token counts, and an "
    "estimated cost in USD (a hardcoded per-model price table - an estimate, not a billing feed)",
    "A model refusal is treated like any other scoring failure and retried the same way - and "
    "its own explanation text is never stored or logged, since a refusal can echo back part of "
    "the flagged input",
    "The OpenAI key reaches only the worker, as a Kubernetes Secret created out-of-band - the "
    "API never receives it, because the API never calls OpenAI",
], size=16)
footer(s, "ConvoScore  ·  OpenAI integration")
add_notes(s, "Two things worth calling out if asked: first, the API genuinely has no code path "
             "that can leak the OpenAI key, because it's never injected into that pod's "
             "environment at all - not a policy, a fact about the deployment. Second, the "
             "refusal-message exclusion was a real finding, not a hypothetical - a refusal's "
             "own explanation text can contain fragments of what was flagged.")

# ------------------------------------------------------------------ #
# Slide 8 - Persistence and human review
# ------------------------------------------------------------------ #
s = add_slide(); set_background(s)
add_kicker_title(s, "Storage and review", "PostgreSQL, durability, and the review UI")
add_bullets(s, [
    "One table, one state machine: pending -> processing -> completed, or -> failed "
    "(validation / transient_exhausted / enqueue_failed)",
    "PostgreSQL runs as a StatefulSet backed by a PersistentVolumeClaim",
    "Durability is demonstrated, not just claimed: the restart demo captures a completed "
    "row's full result before deleting the API, worker, and Postgres pods, then proves the "
    "row is byte-for-byte identical afterward - including the completed_at timestamp",
    "Human review is two server-rendered pages: a list (status, sentiment, risk, created) and "
    "a detail view (full result, timestamps, failure reason if any) - no separate frontend build",
], size=17, width=6.7)
add_picture_fit(s, SCREENSHOTS / "review-detail.png", 7.5, 1.75, 5.2, 5.3)
footer(s, "ConvoScore  ·  Persistence and review")
add_notes(s, "The screenshot on the right is a real conversation from the deployed system, not "
             "a mock. Point out that 'durability is demonstrated' - the restart demo is the "
             "actual proof, and it specifically checks completed_at hasn't changed, because a "
             "re-scored duplicate would also show 'completed' but with a different timestamp.")

# ------------------------------------------------------------------ #
# Slide 9 - Kubernetes and deployment
# ------------------------------------------------------------------ #
s = add_slide(); set_background(s)
add_kicker_title(s, "Deployment", "Kubernetes: one image, two commands")
add_bullets(s, [
    "Multi-stage Docker build: a builder stage installs dependencies into a venv; the runtime "
    "stage copies only that venv plus app/ - no git metadata, tests, or Terraform state",
    "One image, two entrypoints - `python -m uvicorn app.main:app` for the API, "
    "`python -m app.worker` for the worker - the Helm chart just overrides the container command",
    "Non-root (uid 1000), read-only root filesystem, all Linux capabilities dropped, "
    "seccomp RuntimeDefault, no auto-mounted service account token",
    "Startup/liveness/readiness probes tuned to real behavior - readiness runs an actual "
    "SELECT 1, not just a pool checkout, and a generous startup grace period covers a "
    "cold Postgres pull without the liveness probe killing an otherwise-healthy pod",
    "The image tag is always the current git SHA - deploy.sh now refuses to build at all if "
    "any tracked source/Docker/Helm/Terraform file has uncommitted changes, so one tag can "
    "never silently point at two different images",
], size=15.5)
footer(s, "ConvoScore  ·  Kubernetes and deployment")
add_notes(s, "The dirty-tree check is a small thing that closes a real gap: without it, you "
             "could build, deploy, make one more uncommitted edit, and rebuild - now the exact "
             "same SHA-tagged image name refers to two different sets of code, and nobody can "
             "tell which one is actually running from the tag alone.")

# ------------------------------------------------------------------ #
# Slide 10 - Terraform and security
# ------------------------------------------------------------------ #
s = add_slide(); set_background(s)
add_kicker_title(s, "Infrastructure as code", "Terraform, notifications, and least privilege")
add_bullets(s, [
    "Terraform (against LocalStack, standing in for AWS) provisions everything the pipeline "
    "needs: the S3 bucket, the processing queue, its dead-letter queue, and the redrive policy "
    "linking them",
    "An S3 bucket notification wires ObjectCreated events under incoming/ straight to the "
    "SQS queue - this is what makes the direct-upload path work with zero polling",
    "IAM policies describe the intended least-privilege shape per component: the API can only "
    "PutObject under incoming/*; the worker can GetObject there, and can "
    "Receive/Delete/GetAttributes only on the processing queue plus GetUrl/GetAttributes only "
    "on the DLQ - never a wildcard resource",
    "Honest caveat: LocalStack Community does not enforce IAM at the API-call level - these "
    "policies show the real-AWS shape, they don't actually restrict anything in this environment",
    "Kubernetes Secrets are created out-of-band by a small script, referenced by name from the "
    "Helm chart, and never templated into it or committed to Git",
], size=15.5)
footer(s, "ConvoScore  ·  Terraform and security")
add_notes(s, "If asked 'how is least privilege represented' - the honest two-part answer is on "
             "this slide: the IAM policies are written correctly and scoped tightly, but "
             "LocalStack doesn't actually enforce them, so what's being demonstrated here is "
             "the intended shape for real AWS, not a currently-enforced restriction.")

# ------------------------------------------------------------------ #
# Slide 11 - Observability
# ------------------------------------------------------------------ #
s = add_slide(); set_background(s)
add_kicker_title(s, "Observability", "Logs, metrics, dashboard, and what's on watch")
add_bullets(s, [
    "Structured JSON logs, stdout only - never a raw exception message or third-party "
    "exception text, only its type name plus stack frames, enforced through one shared "
    "formatter used everywhere including unhandled-exception paths",
    "Prometheus metrics: processing outcomes, HTTP/processing latency, OpenAI tokens and "
    "estimated cost, DLQ depth, plus two health gauges most dashboards skip",
    "Worker-loop and DLQ-collector 'last progress' timestamps - a bare TCP liveness check only "
    "proves the process is alive, not that it's doing anything; these gauges (and a matching "
    "Prometheus alert) catch a wedged loop that liveness alone would miss",
], size=16, width=6.6)
add_picture_fit(s, SCREENSHOTS / "grafana-dashboard.png", 7.3, 1.7, 5.4, 4.0)
add_picture_fit(s, SCREENSHOTS / "prometheus-alerts.png", 7.3, 5.75, 5.4, 1.4)
footer(s, "ConvoScore  ·  Observability")
add_notes(s, "On-call would watch: DLQ depth rising, the two staleness gauges (worker loop / "
             "DLQ collector), the API's 5xx rate, and the cost counter's rate of change as a "
             "runaway-spend signal. Both screenshots are from the live deployed system. Worth "
             "mentioning if asked: I found and fixed a real bug while preparing these "
             "screenshots - the health gauges are defined in a module shared by both the API "
             "and worker processes, so without a job label filter the API's own "
             "always-zero copy made both alerts fire permanently. Small, but it's exactly the "
             "kind of false-positive that trains an on-call engineer to ignore alerts.")

# ------------------------------------------------------------------ #
# Slide 12 - Live demo flow
# ------------------------------------------------------------------ #
s = add_slide(); set_background(s)
add_kicker_title(s, "Live demo", "Success, direct S3, induced failure, restart")
add_bullets(s, [
    "1. Success: submit via the API and upload directly to S3 - both reach completed with a "
    "real OpenAI result, checked straight against Postgres",
    "2. Induced failure: a deterministic trigger fails scoring on purpose - watch it retry "
    "3 times roughly 60 seconds apart, land in the DLQ, and a healthy conversation submitted "
    "alongside it complete normally the whole time",
    "3. Restart: delete the API, worker, and Postgres pods - watch them come back on the same "
    "image tag, and confirm the pre-restart result is unchanged, down to the timestamp",
    "Exact commands, expected output, timing, and fallback screenshots are in "
    "presentation/DEMO-RUNBOOK.md",
], size=18)
footer(s, "ConvoScore  ·  Live demo")
add_notes(s, "Follow DEMO-RUNBOOK.md in order during the actual session - it has the exact "
             "commands, what should print at each step, how long each part takes, and what to "
             "show instead if a live command doesn't cooperate on the day.")

# ------------------------------------------------------------------ #
# Slide 13 - Tradeoffs and known limitations
# ------------------------------------------------------------------ #
s = add_slide(); set_background(s)
add_kicker_title(s, "Honesty check", "Tradeoffs and known limitations")
add_bullets(s, [
    "No exactly-once processing claim anywhere - SQS is at-least-once by design",
    "A crash between an OpenAI response and the guarded commit can cause one extra paid "
    "OpenAI call for the same conversation - bounded and rare, not eliminated",
    "Prometheus and Grafana storage is emptyDir - metrics/dashboard history do not survive a pod restart",
    "LocalStack Community does not enforce the IAM policies Terraform defines",
    "Pod-restart durability is demonstrated; full cluster deletion or losing the underlying "
    "volume is not, and isn't claimed to be",
    "Grafana uses anonymous viewer access and cost figures are estimates from a hardcoded "
    "pricing table - both explicit, documented tradeoffs for a local take-home, not oversights",
], size=17)
footer(s, "ConvoScore  ·  Tradeoffs and limitations")
add_notes(s, "This slide is deliberately unflattering. Every item here is something I found or "
             "decided, not something a reviewer would have to find themselves - I'd rather say "
             "it out loud than have it discovered.")

# ------------------------------------------------------------------ #
# Slide 14 - What changes for production
# ------------------------------------------------------------------ #
s = add_slide(); set_background(s)
add_kicker_title(s, "Looking ahead", "What changes for real production")
add_bullets(s, [
    "Managed database and queue/storage services (RDS/ElastiCache-equivalent, real SQS/S3) "
    "instead of a self-hosted Postgres pod and LocalStack",
    "Workload identity (e.g. IRSA) instead of static test/test AWS credentials",
    "An external secret manager instead of manually-created Kubernetes Secrets",
    "Authenticated Grafana with real users and roles; persistent metrics/dashboard storage",
    "High availability (multiple replicas, an HA Postgres topology), autoscaling on queue depth",
    "Alert routing to a real on-call system instead of Prometheus-only visibility",
    "Backups, TLS/Ingress, conversation-content retention and privacy controls",
    "A transactional outbox or idempotency-keyed LLM call to close the remaining "
    "duplicate-OpenAI-call window described earlier",
], size=15.5)
footer(s, "ConvoScore  ·  Production roadmap")
add_notes(s, "Frame this as 'what I'd do next with more time and a real budget,' not "
             "'what's missing from this submission' - the take-home was deliberately scoped "
             "smaller than this list on purpose.")

prs.save(str(OUT))
print(f"Wrote {OUT} ({len(prs.slides)} slides)")

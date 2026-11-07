"""
AI Quiz & Question Generator  (Python + Streamlit + Gemini + ReportLab)

BACKEND FLOW (kya-kya hota hai):
  1. Student sidebar mein topic, type, count, difficulty deta hai
  2. build_prompt()        -> in sab se ek instruction (prompt) banta hai
  3. generate_questions()  -> prompt Gemini ko jata hai, JSON wapas aata hai
  4. Validation            -> galat format wale questions hata diye jate hain
  5. session_state         -> questions memory mein save rehte hain
  6. Checkbox selection    -> student jo chahe wo questions chunta hai
  7. build_pdf()           -> sirf selected questions, Q1 se dobara numbering
  8. download_button       -> PDF download
"""

import json
import os
from pathlib import Path
from html import escape as html_escape
from io import BytesIO
from xml.sax.saxutils import escape

import streamlit as st
from dotenv import load_dotenv
from google import genai
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

ENV_PATH = Path(__file__).resolve().parent / ".env"   # app.py ke saath wali .env
load_dotenv(ENV_PATH, override=True)
IS_CLOUD = ENV_PATH.parent.as_posix().startswith("/mount/src")   # Streamlit Cloud par True

LETTERS = ["A", "B", "C", "D"]
QUESTION_TYPES = ["MCQ", "Short Answer", "Long Answer"]
DEFAULT_MODEL = "gemini-3.5-flash"
FALLBACK_MODELS = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.8-flash"]


# ==========================================================================
# PART 1 : AI (Gemini) - questions generate karna
# ==========================================================================
def build_prompt(topic, count, q_type, difficulty, extra):
    """Student ki requirement se AI ke liye instruction banata hai."""
    if q_type == "MCQ":
        fmt = (
            'Each item: {"question": str, "options": [4 strings without A/B/C/D prefix], '
            '"answer": "A" or "B" or "C" or "D", "explanation": str (1 line)}'
        )
    else:
        length = "2-3 line" if q_type == "Short Answer" else "detailed 8-10 line"
        fmt = f'Each item: {{"question": str, "answer": str ({length} model answer)}}'

    return f"""You are an expert exam paper setter.
Create exactly {count} {q_type} questions on the topic: "{topic}".
Difficulty: {difficulty}.
Extra instructions from student: {extra or "None"}.
Questions must be different from each other and factually correct.
Return ONLY a JSON array (no markdown, no extra text). {fmt}"""


def generate_questions(api_key, model, topic, count, q_type, difficulty, extra):
    """Gemini ko call karta hai, JSON parse karta hai, galat questions hataata hai."""
    client = genai.Client(api_key=api_key)
    prompt = build_prompt(topic, count, q_type, difficulty, extra)

    # Model ka naam purana ho gaya ho (404) to agla model try karo
    models_to_try = [model] + [m for m in FALLBACK_MODELS if m != model]
    response, last_error = None, None
    for m in models_to_try:
        try:
            response = client.models.generate_content(
                model=m, contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            break
        except Exception as e:
            if "404" in str(e) or "NOT_FOUND" in str(e):
                last_error = e
                continue
            raise
    if response is None:
        raise last_error

    text = response.text.strip().replace("```json", "").replace("```", "")
    data = json.loads(text)

    clean = []                                   # validation
    for q in data:
        if not isinstance(q, dict) or not q.get("question"):
            continue
        if q_type == "MCQ":
            opts = q.get("options", [])
            if len(opts) != 4 or q.get("answer") not in LETTERS:
                continue
        clean.append(q)
    return clean


# ==========================================================================
# PART 2 : PDF export (ReportLab)
# ==========================================================================
def _page_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 9)
    canvas.setFillColor(colors.grey)
    canvas.drawCentredString(A4[0] / 2, 1.1 * cm, f"Page {doc.page}")
    canvas.restoreState()


def build_pdf(title, questions, q_type, with_answers):
    """questions = sirf selected questions. Numbering 1 se dobara start hoti hai."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, title=title,
        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
    )
    base = getSampleStyleSheet()
    purple = colors.HexColor("#4B3FCF")
    title_style = ParagraphStyle("T", parent=base["Title"], textColor=purple, fontSize=22)
    sub_style = ParagraphStyle("S", parent=base["Normal"], textColor=colors.grey, alignment=1)
    q_style = ParagraphStyle("Q", parent=base["Normal"], fontSize=11, leading=15, spaceBefore=10)
    o_style = ParagraphStyle("O", parent=base["Normal"], fontSize=10.5, leading=14, leftIndent=22)
    h_style = ParagraphStyle("H", parent=base["Heading1"], textColor=purple)

    story = [
        Paragraph(escape(title), title_style),
        Paragraph(f"Total Questions: {len(questions)}", sub_style),
        Spacer(1, 14),
    ]
    for n, q in enumerate(questions, start=1):
        story.append(Paragraph(f"<b>Q{n}.</b> {escape(q['question'])}", q_style))
        if q_type == "MCQ":
            for letter, opt in zip(LETTERS, q["options"]):
                story.append(Paragraph(f"({letter}) {escape(str(opt))}", o_style))
        else:
            story.append(Spacer(1, 40 if q_type == "Short Answer" else 120))

    if with_answers:
        story += [Spacer(1, 24), Paragraph("Answer Key", h_style)]
        for n, q in enumerate(questions, start=1):
            if q_type == "MCQ":
                idx = LETTERS.index(q["answer"])
                text = f"<b>Q{n}.</b> ({q['answer']}) {escape(str(q['options'][idx]))}"
                if q.get("explanation"):
                    text += f"<br/><i>{escape(q['explanation'])}</i>"
            else:
                text = f"<b>Q{n}.</b> {escape(q.get('answer', ''))}"
            story += [Paragraph(text, o_style), Spacer(1, 4)]

    doc.build(story, onFirstPage=_page_footer, onLaterPages=_page_footer)
    return buffer.getvalue()


# ==========================================================================
# PART 3 : Selection helpers (session_state mein checkbox ki state)
# ==========================================================================
def set_all(value):
    for i in range(len(st.session_state.questions)):
        st.session_state[f"sel_{i}"] = value


def pick_first(n):
    for i in range(len(st.session_state.questions)):
        st.session_state[f"sel_{i}"] = i < n


def selected_questions():
    return [
        q for i, q in enumerate(st.session_state.questions)
        if st.session_state.get(f"sel_{i}", False)
    ]


# ==========================================================================
# PART 4 : Look & feel (CSS)
# ==========================================================================
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"], .stMarkdown, button, input, textarea {font-family:'Inter',sans-serif;}
[data-testid="stAppDeployButton"], .stDeployButton {display:none;}
.footer{text-align:center;color:#8a8fa3;font-size:.8rem;margin-top:36px;padding:14px 0;border-top:1px solid rgba(108,92,231,.2);}
.hero{background:linear-gradient(135deg,#6C5CE7 0%,#00B8D9 100%);
      padding:28px 34px;border-radius:20px;margin-bottom:20px;
      box-shadow:0 8px 24px rgba(108,92,231,.25);}
.hero h1{color:#fff !important;margin:0;font-size:2.1rem;padding:0;}
.hero p{color:rgba(255,255,255,.92) !important;margin:8px 0 0;font-size:1.02rem;}
.steps{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px;}
.steps span{background:rgba(255,255,255,.2);color:#fff;padding:5px 14px;
            border-radius:999px;font-size:.85rem;}
.opt{display:block;padding:7px 14px;margin:5px 0 5px 28px;border-radius:12px;
     background:rgba(108,92,231,.10);border:1px solid rgba(108,92,231,.25);}
.opt b{color:#6C5CE7;margin-right:6px;}
.tag{display:inline-block;padding:2px 12px;border-radius:999px;font-size:.78rem;
     background:rgba(0,184,217,.15);color:#00A3C4;font-weight:600;margin-right:6px;}
div[data-testid="stMetric"]{background:rgba(108,92,231,.08);
     border:1px solid rgba(108,92,231,.2);padding:12px 16px;border-radius:14px;}
.stButton>button,.stDownloadButton>button{width:100%;border-radius:12px;font-weight:600;}
.stDownloadButton>button{background:linear-gradient(135deg,#6C5CE7,#00B8D9);
     color:#fff;border:none;padding:.7rem 1rem;}
section[data-testid="stSidebar"]{border-right:1px solid rgba(108,92,231,.2);}
</style>
"""

# ==========================================================================
# PART 5 : UI (Streamlit)
# ==========================================================================
st.set_page_config(page_title="AI Quiz Generator", page_icon="📝", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)
st.markdown(
    """<div class="hero">
    <h1>📝 AI Quiz &amp; Question Generator</h1>
    <p>Generate custom quizzes on any topic, choose the questions you need, and export them as a clean, print-ready PDF.</p>
    <div class="steps"><span>① Enter topic</span><span>② Generate</span>
    <span>③ Select questions</span><span>④ Export PDF</span></div></div>""",
    unsafe_allow_html=True,
)


def get_api_key():
    """Key sirf backend se aati hai (.env ya Streamlit Secrets), UI mein kabhi nahi dikhti."""
    try:
        if "GEMINI_API_KEY" in st.secrets:
            return st.secrets["GEMINI_API_KEY"]
    except Exception:
        pass
    return os.getenv("GEMINI_API_KEY", "")


def has_placeholder_key(value):
    if not value:
        return True
    v = str(value).strip().lower()
    return v in ("your_api_key_here", "your_api_key", "api_key_here", "replace_me") \
        or "your_api" in v or "api_key_here" in v or len(v) < 20


def save_api_key(key):
    """Key ko .env file mein hamesha ke liye save karta hai (sirf ek baar)."""
    key = key.strip()
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    lines = [ln for ln in lines if not ln.strip().startswith("GEMINI_API_KEY")]
    lines.append(f"GEMINI_API_KEY={key}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ["GEMINI_API_KEY"] = key

model = DEFAULT_MODEL

if "questions" not in st.session_state:       # memory: generated questions
    st.session_state.questions = []
    st.session_state.meta = {}


# ---------------- One-time key setup (sirf tab jab key missing ya invalid ho) ----------------
api_key = get_api_key()
if has_placeholder_key(api_key) or st.session_state.get("show_key_setup"):
    if IS_CLOUD:      # online app par koi bhi visitor key change na kar sake
        st.error("The AI service is temporarily unavailable. Please contact the administrator.")
        st.stop()
    st.warning("🔑 One-time setup: the Gemini API key is missing or invalid. "
               "Paste it once below. It is saved in the .env file and never asked again.")
    new_key = st.text_input("Gemini API key", type="password",
                            help="Create a free key at aistudio.google.com")
    if st.button("💾 Save key permanently"):
        if has_placeholder_key(new_key):
            st.error("That does not look like a valid key. Please copy the full key.")
        else:
            save_api_key(new_key)
            st.session_state.show_key_setup = False
            st.rerun()
    st.stop()

# ---------------- Sidebar ----------------
with st.sidebar:
    st.header("🎯 Quiz Requirements")
    topic = st.text_input("Topic", placeholder="e.g. Python Loops, DBMS, Data Structures")
    q_type = st.selectbox("Question type", QUESTION_TYPES)
    count = st.slider("Number of questions", 5, 50, 30)
    difficulty = st.selectbox("Difficulty", ["Easy", "Medium", "Hard", "Mixed"], index=1)
    extra = st.text_area("Extra instructions (optional)",
                         placeholder="e.g. exam-oriented, numerical-based, include code snippets")
    generate = st.button("🚀 Generate Questions", type="primary")

# ---------------- Generate button ka kaam ----------------
if generate:
    if has_placeholder_key(api_key):
        st.error("Invalid Gemini API key.")
    elif not topic.strip():
        st.error("Please enter a topic.")
    else:
        with st.spinner("🤖 Generating questions, please wait..."):
            try:
                qs = generate_questions(api_key, model, topic.strip(), count, q_type, difficulty, extra)
                if not qs:
                    st.error("No valid questions were generated. Please try again.")
                else:
                    st.session_state.questions = qs
                    st.session_state.meta = {"topic": topic.strip(), "type": q_type, "difficulty": difficulty}
                    for i in range(len(qs)):                    # by default sab selected
                        st.session_state[f"sel_{i}"] = True
                    for k in [k for k in st.session_state if k.startswith("ans_")]:
                        del st.session_state[k]
            except json.JSONDecodeError:
                st.error("The AI returned an unexpected format. Please click Generate again.")
            except Exception as e:
                msg = str(e).lower()
                if "api key" in msg or "api_key" in msg or "invalid_argument" in msg or "permission_denied" in msg:
                    st.session_state.show_key_setup = True
                    st.error("The Gemini API key is invalid or expired. Reload the page and paste a valid key once.")
                elif "unavailable" in msg or "503" in msg or "429" in msg or "rate limit" in msg:
                    st.error("The AI service is temporarily busy. Please wait a moment and click Generate again.")
                else:
                    st.error(f"Error: {e}")

questions = st.session_state.questions
meta = st.session_state.meta

if not questions:
    st.info("👈 Enter a topic and your requirements in the sidebar, then click **Generate Questions**.")
    st.stop()

q_type = meta["type"]
chosen = selected_questions()

# ---------------- Stats row ----------------
m1, m2, m3, m4 = st.columns(4)
m1.metric("📚 Topic", meta["topic"])
m2.metric("❓ Generated", len(questions))
m3.metric("✅ Selected", len(chosen))
m4.metric("🎚️ Difficulty", meta["difficulty"])
st.progress(len(chosen) / len(questions), text=f"{len(chosen)} / {len(questions)} questions selected")

tab1, tab2 = st.tabs(["✅ Select & Export", "🧪 Practice Quiz"])

# ---------------- TAB 1 : select + export ----------------
with tab1:
    c1, c2, c3, c4 = st.columns([1, 1, 1, 1.4])
    c1.button("Select All", on_click=set_all, args=(True,))
    c2.button("Clear All", on_click=set_all, args=(False,))
    n_first = c4.number_input("First N questions", 1, len(questions), min(10, len(questions)))
    c3.button("Select first N", on_click=pick_first, args=(n_first,))

    for i, q in enumerate(questions):
        with st.container(border=True):
            st.checkbox(f"**Q{i + 1}.** {q['question']}", key=f"sel_{i}")
            if q_type == "MCQ":
                opts_html = "".join(
                    f'<span class="opt"><b>({l})</b>{html_escape(str(o))}</span>'
                    for l, o in zip(LETTERS, q["options"])
                )
                st.markdown(opts_html, unsafe_allow_html=True)
            with st.expander("💡 View answer"):
                if q_type == "MCQ":
                    st.markdown(f'<span class="tag">Answer: ({q["answer"]})</span> '
                                f'{html_escape(str(q["options"][LETTERS.index(q["answer"])]))}',
                                unsafe_allow_html=True)
                    if q.get("explanation"):
                        st.caption(q["explanation"])
                else:
                    st.write(q.get("answer", ""))

    st.divider()
    st.subheader(f"📄 Export PDF · {len(chosen)} question(s) selected")
    pdf_title = st.text_input("Document title", value=f"{meta['topic']} - {q_type} Practice Set")
    with_answers = st.checkbox("Include answer key at the end of the PDF", value=True)

    if chosen:
        pdf_bytes = build_pdf(pdf_title, chosen, q_type, with_answers)
        st.download_button("⬇️ Download PDF", data=pdf_bytes,
                           file_name=f"{meta['topic'].replace(' ', '_')}_quiz.pdf",
                           mime="application/pdf")
        st.caption("Selected questions are renumbered Q1, Q2, Q3… in the PDF.")
    else:
        st.warning("Select at least one question to export.")

# ---------------- TAB 2 : attempt quiz ----------------
with tab2:
    if q_type != "MCQ":
        st.info("Practice mode is available for MCQ only. Choose MCQ as the question type in the sidebar.")
    elif not chosen:
        st.warning("Select questions in the 'Select & Export' tab first.")
    else:
        st.write(f"Practice quiz with **{len(chosen)}** selected question(s):")
        with st.form("quiz_form"):
            for n, q in enumerate(chosen, start=1):
                st.radio(f"Q{n}. {q['question']}",
                         [f"({l}) {o}" for l, o in zip(LETTERS, q["options"])],
                         index=None, key=f"ans_{n}")
            submitted = st.form_submit_button("Submit Quiz")

        if submitted:
            score = sum(
                1 for n, q in enumerate(chosen, start=1)
                if st.session_state.get(f"ans_{n}") and st.session_state[f"ans_{n}"][1] == q["answer"]
            )
            pct = score / len(chosen) * 100
            st.progress(score / len(chosen), text=f"Score: {score} / {len(chosen)}  ({pct:.0f}%)")
            if pct >= 75:
                st.success("🏆 Excellent work!")
            elif pct >= 40:
                st.warning("👍 Good effort. A little more practice will help.")
            else:
                st.error("📖 Please review this topic and try again.")
            for n, q in enumerate(chosen, start=1):
                picked = st.session_state.get(f"ans_{n}")
                ok = picked and picked[1] == q["answer"]
                st.write(f"{'✅' if ok else '❌'} **Q{n}** · Correct answer: ({q['answer']}). {q.get('explanation', '')}")


st.markdown('<div class="footer">AI Quiz & Question Generator · Built with Python, Streamlit and Gemini</div>', unsafe_allow_html=True)
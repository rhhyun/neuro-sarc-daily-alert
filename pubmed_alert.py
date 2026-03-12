import datetime
import html
import os
import re
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from Bio import Entrez
from openai import OpenAI

# =========================
# Environment configuration
# =========================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
TO_EMAIL = os.getenv("TO_EMAIL", "rhhyun@gmail.com")
NCBI_EMAIL = os.getenv("NCBI_EMAIL")

MAX_RESULTS_NEURAL = int(os.getenv("MAX_RESULTS_NEURAL", "5"))
MAX_RESULTS_SARC = int(os.getenv("MAX_RESULTS_SARC", "5"))
MAX_RESULTS_AI = int(os.getenv("MAX_RESULTS_AI", "3"))
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
SUMMARY_DELAY_SECONDS = int(os.getenv("SUMMARY_DELAY_SECONDS", "8"))
SUMMARY_BATCH_SIZE = int(os.getenv("SUMMARY_BATCH_SIZE", "5"))
USE_LOCAL_SUMMARY_ONLY = os.getenv("USE_LOCAL_SUMMARY_ONLY", "false").lower() == "true"
SHOULD_ENFORCE_SCHEDULE = os.getenv("ENFORCE_MWS_SCHEDULE", "false").lower() == "true"

REQUIRED_ENV = {
    "OPENAI_API_KEY": OPENAI_API_KEY,
    "GMAIL_USER": GMAIL_USER,
    "GMAIL_APP_PASSWORD": GMAIL_APP_PASSWORD,
    "NCBI_EMAIL": NCBI_EMAIL,
}

missing = [key for key, value in REQUIRED_ENV.items() if not value]
if missing:
    raise ValueError(f"🚨 필수 환경 변수가 누락되었습니다: {', '.join(missing)}")

client = OpenAI(api_key=OPENAI_API_KEY)
Entrez.email = NCBI_EMAIL

HIGH_IMPACT_JOURNALS = [
    "Nature",
    "Science",
    "Cell",
    "Nature Neuroscience",
    "Neuron",
    "Nature Medicine",
    "Lancet Neurology",
    "Biomaterials",
    "Advanced Materials",
    "J Cachexia Sarcopenia Muscle",
    "Stem Cell Reports",
    "Clinical Rehabilitation",
]

today = datetime.date.today()
yesterday = today - datetime.timedelta(days=1)
date_str = yesterday.strftime("%Y/%m/%d")
yesterday_date = yesterday.strftime("%Y-%m-%d")

NEURAL_QUERY = (
    f'("spinal cord injury"[Title/Abstract] OR "peripheral nerve injury"[Title/Abstract] '
    f'OR electroceutical*[Title/Abstract] OR "drug repositioning"[Title/Abstract] '
    f'OR "gene therapy"[Title/Abstract] OR "biomaterial scaffold"[Title/Abstract] '
    f'OR "neural regeneration"[Title/Abstract] OR "neural plasticity"[Title/Abstract] '
    f'OR "axon regeneration"[Title/Abstract] OR "electrical stimulation"[Title/Abstract] '
    f'OR "AI"[Title/Abstract] OR "robot"[Title/Abstract]) '
    f'AND ("{date_str}"[PDAT] : "{date_str}"[PDAT])'
)

SARC_QUERY = (
    f'("sarcopenia"[Title/Abstract] OR "muscle atrophy"[Title/Abstract] OR "muscle wasting"[Title/Abstract] '
    f'OR cachexia[Title/Abstract]) AND ("drug repositioning"[Title/Abstract] OR repositioning[Title/Abstract] '
    f'OR rehabilitation[Title/Abstract] OR "physical therapy"[Title/Abstract] OR "electrical stimulation"[Title/Abstract] '
    f'OR NMES[Title/Abstract] OR FES[Title/Abstract] OR electrostimulation[Title/Abstract] OR AI[Title/Abstract] '
    f'OR robot[Title/Abstract]) AND ("{date_str}"[PDAT] : "{date_str}"[PDAT])'
)

AI_QUERY = (
    f'(("medical AI"[Title/Abstract] OR "artificial intelligence"[Title/Abstract]) '
    f'AND ("spinal cord"[Title/Abstract] OR "sarcopenia"[Title/Abstract] OR brain[Title/Abstract] '
    f'OR "electrical stimulation"[Title/Abstract] OR "signal detection"[Title/Abstract])) '
    f'AND ("{date_str}"[PDAT] : "{date_str}"[PDAT])'
)


def should_run_today() -> bool:
    if not SHOULD_ENFORCE_SCHEDULE:
        return True
    return today.weekday() in {0, 2, 5}


def chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def find_key(obj, key, default="Unknown"):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            result = find_key(value, key, default)
            if result != default:
                return result
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            result = find_key(item, key, default)
            if result != default:
                return result
    return default


def clean_text(value: str) -> str:
    text = str(value or "").strip()
    return re.sub(r"\s+", " ", text)


def fetch_papers(query, topic, max_results=30):
    print(f"Fetching papers for {topic}... Query snippet: {query[:90]}...")
    try:
        handle = Entrez.esearch(db="pubmed", term=query, retmax=max_results, sort="pub+date")
        record = Entrez.read(handle)
        handle.close()
        pmids = record.get("IdList", [])
    except Exception as error:
        print(f"Entrez search error ({topic}): {error}")
        return []

    papers = []
    for pmid in pmids:
        try:
            handle = Entrez.efetch(db="pubmed", id=pmid, retmode="xml")
            raw_record = Entrez.read(handle)
            handle.close()

            if hasattr(raw_record, "keys"):
                raw_record = dict(raw_record)
            if isinstance(raw_record, dict) and "PubmedArticleSet" in raw_record:
                article_set = raw_record["PubmedArticleSet"]
                article = article_set[0] if isinstance(article_set, (list, tuple)) and article_set else article_set
            else:
                article = raw_record

            if hasattr(article, "keys"):
                article = dict(article)

            title = clean_text(find_key(article, "ArticleTitle", "No Title"))
            journal = clean_text(find_key(article, "Title", "Unknown Journal"))
            abstract_section = find_key(article, "Abstract", {})
            abstract_list = find_key(abstract_section, "AbstractText", [])
            abstract = (
                " ".join(clean_text(part) for part in abstract_list)
                if isinstance(abstract_list, (list, tuple))
                else clean_text(abstract_list)
            )

            is_high_impact = any(j.lower() in journal.lower() for j in HIGH_IMPACT_JOURNALS)

            papers.append(
                {
                    "pmid": pmid,
                    "title": title,
                    "journal": journal,
                    "abstract": abstract,
                    "link": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    "topic": topic,
                    "is_high_impact": is_high_impact,
                }
            )
        except Exception as error:
            print(f"Error processing PMID {pmid}: {error}")
    return papers


def simple_fallback_summary(abstract: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", abstract)
    selected = [s.strip() for s in sentences if len(s.strip()) > 30][:3]
    if not selected:
        return "- 핵심 내용 추출이 제한되었습니다. PubMed 링크에서 원문 초록을 확인해 주세요."

    if len(selected) == 1:
        return "\n".join(
            [
                f"- 연구 배경/목적: {selected[0]}",
                "- 주요 결과/해석: 초록 내 핵심 결과를 확인했으나, 자동 요약 품질 보전을 위해 원문 초록 확인을 권장합니다.",
            ]
        )

    return "\n".join(
        [
            f"- 연구 배경/목적: {selected[0]}",
            f"- 주요 결과: {selected[1]}",
            f"- 임상/전임상 시사점: {selected[2]}" if len(selected) > 2 else "- 임상/전임상 시사점: 기능 회복(functional recovery) 또는 기전적 의미를 원문에서 추가 확인하세요.",
        ]
    )


def sanitize_summary(raw_summary: str) -> str:
    text = raw_summary or ""
    text = text.replace("<", "미만").replace(">", "초과")
    text = text.replace("**", "").replace("*", "")
    text = re.sub(r"^\s*(다음은 제공된 초록.*?요약입니다\.?|제공된 초록을 바탕으로 한 요약입니다\.?)\s*", "", text, flags=re.I)
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    bullet_lines = []
    for line in lines:
        line = re.sub(r"^[•·]\s*", "- ", line)
        if not line.startswith("-"):
            line = f"- {line}"
        bullet_lines.append(line)

    bullet_lines = bullet_lines[:3] if bullet_lines else ["- 요약 결과가 비어 있습니다."]
    return "\n".join(bullet_lines)


def summary_to_html(summary_text: str) -> str:
    lines = [line.strip() for line in summary_text.splitlines() if line.strip()]
    items = []
    for line in lines:
        content = line[1:].strip() if line.startswith("-") else line
        items.append(f"<li>{html.escape(content)}</li>")
    return "<ul style='margin:6px 0 0 20px; padding:0;'>" + "".join(items) + "</ul>"


def parse_batch_summary(raw_text: str):
    parsed = {}
    current_pmid = None
    lines = [line.rstrip() for line in (raw_text or "").splitlines()]

    for line in lines:
        match = re.match(r"^PMID\s*:\s*(\d+)\s*$", line.strip(), flags=re.I)
        if match:
            current_pmid = match.group(1)
            parsed[current_pmid] = []
            continue

        if not current_pmid:
            continue

        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("-"):
            stripped = f"- {stripped}"
        parsed[current_pmid].append(stripped)

    summary_map = {}
    for pmid, bullets in parsed.items():
        text = "\n".join(bullets[:3]) if bullets else "- 요약 결과가 비어 있습니다."
        summary_map[pmid] = sanitize_summary(text)
    return summary_map


def summarize_batch(papers):
    entries = []
    fallback_map = {}
    for paper in papers:
        pmid = paper["pmid"]
        abstract = paper["abstract"]
        if not abstract.strip() or abstract == "Unknown":
            fallback_map[pmid] = "- 제공된 Abstract가 없습니다."
            continue

        fallback_map[pmid] = simple_fallback_summary(abstract)
        entries.append(
            f"PMID: {pmid}\n"
            f"Title: {paper['title']}\n"
            f"Abstract: {abstract}\n"
        )

    if not entries:
        return fallback_map

    if USE_LOCAL_SUMMARY_ONLY:
        print("USE_LOCAL_SUMMARY_ONLY=true, skipping OpenAI calls and using abstract fallback summaries.")
        return fallback_map

    batch_prompt = (
        "아래 논문들의 abstract만 근거로 요약하세요.\n"
        "각 논문마다 2-3개 bullet만 작성하세요.\n"
        "출력 형식은 반드시 아래를 반복:\n"
        "PMID: <숫자>\n"
        "- 요약1\n"
        "- 요약2\n"
        "(필요 시 - 요약3)\n"
        "마크다운 bold/HTML 금지.\n\n"
        + "\n".join(entries)
    )

    for attempt in range(4):
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a senior clinical researcher. "
                            "Return plain text only. "
                            "Professional Korean, keep key scientific terms in English."
                        ),
                    },
                    {"role": "user", "content": batch_prompt},
                ],
                temperature=0.2,
                max_tokens=1400,
            )
            raw_text = response.choices[0].message.content.strip() if response.choices else ""
            parsed_map = parse_batch_summary(raw_text)

            merged = {}
            for paper in papers:
                pmid = paper["pmid"]
                merged[pmid] = parsed_map.get(pmid, fallback_map.get(pmid, "- 요약 생성 실패"))

            print(f"Batch summary generated for {len(papers)} papers. Waiting {SUMMARY_DELAY_SECONDS}s...")
            time.sleep(SUMMARY_DELAY_SECONDS)
            return merged
        except Exception as error:
            error_msg = str(error)
            if "429" in error_msg or "rate" in error_msg.lower() or "quota" in error_msg.lower():
                wait_seconds = 20 + (attempt * 20)
                print(f"OpenAI rate-limit/quota issue. Waiting {wait_seconds}s... (Attempt {attempt + 1}/4)")
                time.sleep(wait_seconds)
                continue
            print(f"OpenAI batch summarize error: {error_msg}")
            break

    return fallback_map


def get_top_papers(all_papers, count=2):
    high_impact = [paper for paper in all_papers if paper["is_high_impact"]]
    others = [paper for paper in all_papers if not paper["is_high_impact"]]
    return (high_impact + others)[:count]


def build_summary_map(papers):
    unique = {}
    for paper in papers:
        unique[paper["pmid"]] = paper

    summary_map = {}
    unique_papers = list(unique.values())
    # 큰 배치가 토큰 초과를 일으켜 전체 실패할 수 있으므로, 기본값(5) 기반 소배치를 유지
    for batch in chunked(unique_papers, max(1, SUMMARY_BATCH_SIZE)):
        batch_map = summarize_batch(batch)
        summary_map.update(batch_map)
    return summary_map


def format_paper_html(paper, summary_map, index=None):
    summary_html = summary_to_html(summary_map.get(paper["pmid"], "- 요약 생성 실패"))
    index_str = f"{index}. " if index else ""
    journal = html.escape(paper["journal"])
    journal_str = (
        f"<strong><span style='color:#b30000;'>{journal} (High-Impact)</span></strong>"
        if paper["is_high_impact"]
        else journal
    )

    return f"""
    <div style="margin-bottom: 25px; padding: 15px; border: 1px solid #ddd; border-radius: 5px; background-color: #fafafa;">
        <h4 style="margin-top: 0; color: #2c3e50;">{index_str}{html.escape(paper['title'])}</h4>
        <p style="margin: 5px 0; font-size: 0.9em;">
            <strong>Journal:</strong> {journal_str}<br>
            <strong>PMID:</strong> <a href="{paper['link']}" target="_blank" style="color: #2980b9; text-decoration: underline;">{paper['pmid']}</a> | <a href="{paper['link']}" target="_blank" style="color: #2980b9; text-decoration: underline;">PubMed 링크</a>
        </p>
        <div style="margin-top: 10px; padding-top: 10px; border-top: 1px dashed #ccc;">
            <strong style="color: #333;">💡 핵심 요약:</strong>
            <div style="margin-top: 5px; color: #444; line-height: 1.6;">{summary_html}</div>
        </div>
    </div>
"""




def format_paper_text(paper, summary_map, index=None):
    summary = summary_map.get(paper["pmid"], "- 요약 생성 실패")
    index_str = f"{index}. " if index else ""
    return (
        f"{index_str}{paper['title']}\n"
        f"Journal: {paper['journal']}" + (" (High-Impact)" if paper["is_high_impact"] else "") + "\n"
        f"PMID: {paper['pmid']} | {paper['link']}\n"
        f"핵심 요약:\n{summary}\n"
    )


def build_plaintext_email(neural_papers, sarc_papers, ai_papers, top_papers, summary_map):
    lines = [
        f"📊 {yesterday_date} PubMed 최신 동향 리포트",
        "연구자님, 지정하신 전문 분야의 최신 논문 검색 결과입니다. 학술적 근거와 명확한 사실에 기반하여 요약되었습니다.",
        "",
    ]

    if top_papers:
        lines.extend(["🌟 오늘의 주요 논문 Top 2", "High-impact 저널 및 검색 적합도를 우선으로 선정되었습니다.", ""])
        for paper in top_papers:
            lines.append(format_paper_text(paper, summary_map))

    lines.extend(["🧠 신경재생·가소성 섹션 (전체)", ""])
    if neural_papers:
        for idx, paper in enumerate(neural_papers, 1):
            lines.append(format_paper_text(paper, summary_map, index=idx))
    else:
        lines.append("해당 분야의 새로운 논문이 없습니다.\n")

    lines.extend(["💪 사르코페니아 섹션 (전체)", ""])
    if sarc_papers:
        for idx, paper in enumerate(sarc_papers, 1):
            lines.append(format_paper_text(paper, summary_map, index=idx))
    else:
        lines.append("해당 분야의 새로운 논문이 없습니다.\n")

    lines.extend(["🤖 의료 AI·뇌 전기자극/진단 섹션", ""])
    if ai_papers:
        for idx, paper in enumerate(ai_papers, 1):
            lines.append(format_paper_text(paper, summary_map, index=idx))
    else:
        lines.append("해당 분야의 새로운 논문이 없습니다.\n")

    lines.append("본 이메일은 PubMed + OpenAI API 기반 자동화 리포트입니다.")
    return "\n".join(lines)

def send_email(neural_papers, sarc_papers, ai_papers):
    all_papers = neural_papers + sarc_papers + ai_papers
    if not all_papers:
        print("No papers found for today's query.")
        return

    summary_map = build_summary_map(all_papers)
    top_papers = get_top_papers(all_papers, count=2)

    body_html = f"""<html><body style="font-family: 'Malgun Gothic', dotum, sans-serif; line-height: 1.6; color: #333; max-width: 800px; margin: 0 auto;">
    <h2 style="color: #2c3e50; border-bottom: 2px solid #2c3e50; padding-bottom: 10px;">📊 {yesterday_date} PubMed 최신 동향 리포트</h2>
    <p>연구자님, 지정하신 전문 분야의 최신 논문 검색 결과입니다. 학술적 근거와 명확한 사실에 기반하여 요약되었습니다.</p>
"""

    if top_papers:
        body_html += """
    <h3 style="color: #c0392b; margin-top: 30px; padding: 5px 10px; background-color: #fdebd0; border-left: 5px solid #c0392b;">🌟 오늘의 주요 논문 Top 2</h3>
    <p style="font-size: 0.9em; color: #666;">* High-impact 저널 및 검색 적합도를 우선으로 선정되었습니다.</p>
"""
        for paper in top_papers:
            body_html += format_paper_html(paper, summary_map)

    body_html += """
    <h3 style="color: #2980b9; margin-top: 40px; padding: 5px 10px; background-color: #ebf5fb; border-left: 5px solid #2980b9;">🧠 신경재생·가소성 섹션 (전체)</h3>
"""
    if neural_papers:
        for idx, paper in enumerate(neural_papers, 1):
            body_html += format_paper_html(paper, summary_map, index=idx)
    else:
        body_html += "<p>해당 분야의 새로운 논문이 없습니다.</p>"

    body_html += """
    <h3 style="color: #27ae60; margin-top: 40px; padding: 5px 10px; background-color: #e9f7ef; border-left: 5px solid #27ae60;">💪 사르코페니아 섹션 (전체)</h3>
"""
    if sarc_papers:
        for idx, paper in enumerate(sarc_papers, 1):
            body_html += format_paper_html(paper, summary_map, index=idx)
    else:
        body_html += "<p>해당 분야의 새로운 논문이 없습니다.</p>"

    body_html += """
    <h3 style="color: #7d3c98; margin-top: 40px; padding: 5px 10px; background-color: #f5eef8; border-left: 5px solid #7d3c98;">🤖 의료 AI·뇌 전기자극/진단 섹션</h3>
"""
    if ai_papers:
        for idx, paper in enumerate(ai_papers, 1):
            body_html += format_paper_html(paper, summary_map, index=idx)
    else:
        body_html += "<p>해당 분야의 새로운 논문이 없습니다.</p>"

    body_html += """
    <hr style="margin-top: 40px; border: 0; border-top: 1px solid #eee;">
    <p style="text-align: center; color: #888; font-size: 0.85em;"><em>본 이메일은 PubMed + OpenAI API 기반 자동화 리포트입니다.</em></p>
    </body></html>
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[{yesterday_date}] 주요 논문 요약: Neuro + Sarcopenia + Medical AI (총 {len(all_papers)}건)"
    msg["From"] = f"Neuro-Sarc Alert <{GMAIL_USER}>"
    msg["To"] = TO_EMAIL
    body_text = build_plaintext_email(neural_papers, sarc_papers, ai_papers, top_papers, summary_map)
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.send_message(msg)
    print("✅ HTML Email sent successfully!")


def main():
    print("=== Neuro-Sarc Alert Script Started ===")
    print(f"Searching papers for date: {yesterday_date}")

    if not should_run_today():
        print("Today is not Monday/Wednesday/Saturday. Skipping run.")
        return

    neural = fetch_papers(NEURAL_QUERY, topic="neural", max_results=MAX_RESULTS_NEURAL)
    sarc = fetch_papers(SARC_QUERY, topic="sarcopenia", max_results=MAX_RESULTS_SARC)
    ai = fetch_papers(AI_QUERY, topic="medical-ai", max_results=MAX_RESULTS_AI)

    send_email(neural, sarc, ai)
    print("=== Script completed SUCCESSFULLY ===")


if __name__ == "__main__":
    main()

import os
import time
import datetime
import traceback
from Bio import Entrez
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

# 1. 환경 변수 설정 (에러 방지 강화)
GEMINI_API_KEY = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
TO_EMAIL = os.getenv("TO_EMAIL")
NCBI_EMAIL = os.getenv("NCBI_EMAIL")

if not GEMINI_API_KEY:
    raise ValueError("🚨 치명적 에러: API 키를 찾을 수 없습니다! GitHub Repository secrets에 키가 정확히 등록되었는지 확인하세요.")

# Gemini API 및 Entrez 초기화
genai.configure(api_key=GEMINI_API_KEY)
Entrez.email = NCBI_EMAIL

HIGH_IMPACT_JOURNALS = [
    "Nature", "Science", "Cell", "Nature Neuroscience", "Neuron", "Nature Medicine",
    "Lancet Neurology", "Biomaterials", "Advanced Materials", "J Cachexia Sarcopenia Muscle",
    "Experimental Cell Research", "Journal of Spinal Cord Medicine", "Neural Regeneration Research",
    "Rehabilitation", "Frontiers in Pharmacology", "Nutrients", "Stem Cell Reports"
]

today = datetime.date.today()
yesterday = today - datetime.timedelta(days=1)
date_str = yesterday.strftime("%Y/%m/%d")
yesterday_date = yesterday.strftime("%Y-%m-%d")

NEURAL_QUERY = f'("spinal cord injury"[Title/Abstract] OR "peripheral nerve injury"[Title/Abstract] OR electroceutical*[Title/Abstract] OR "drug repositioning"[Title/Abstract] OR "gene therapy"[Title/Abstract] OR "biomaterial scaffold"[Title/Abstract] OR "neural regeneration"[Title/Abstract] OR "neural plasticity"[Title/Abstract] OR "axon regeneration"[Title/Abstract]) AND ("{date_str}"[PDAT] : "{date_str}"[PDAT])'

SARC_QUERY = f'("sarcopenia"[Title/Abstract] OR "muscle atrophy"[Title/Abstract] OR "muscle wasting"[Title/Abstract]) AND ("drug repositioning"[Title/Abstract] OR repositioning[Title/Abstract] OR rehabilitation[Title/Abstract] OR "physical therapy"[Title/Abstract] OR "electrical stimulation"[Title/Abstract] OR NMES[Title/Abstract] OR FES[Title/Abstract] OR electrostimulation[Title/Abstract]) AND ("{date_str}"[PDAT] : "{date_str}"[PDAT])'

def find_key(obj, key, default="Unknown"):
    if isinstance(obj, dict):
        if key in obj: return obj[key]
        for v in obj.values():
            result = find_key(v, key, default)
            if result != default: return result
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            result = find_key(item, key, default)
            if result != default: return result
    return default

def fetch_papers(query, max_results=30):
    print(f"Fetching papers... Query snippet: {query[:100]}...")
    try:
        handle = Entrez.esearch(db="pubmed", term=query, retmax=max_results, sort="pub+date")
        record = Entrez.read(handle)
        handle.close()
        pmids = record.get("IdList", [])
    except Exception as e:
        print(f"Entrez search error: {e}")
        return []

    papers = []
    for pmid in pmids:
        try:
            handle = Entrez.efetch(db="pubmed", id=pmid, retmode="xml")
            raw_record = Entrez.read(handle)
            handle.close()

            if hasattr(raw_record, 'keys'): raw_record = dict(raw_record)
            if isinstance(raw_record, dict) and "PubmedArticleSet" in raw_record:
                article_set = raw_record["PubmedArticleSet"]
                article = article_set[0] if isinstance(article_set, (list, tuple)) and article_set else article_set
            else:
                article = raw_record
            if hasattr(article, 'keys'): article = dict(article)

            title = find_key(article, "ArticleTitle", "No Title")
            journal = find_key(article, "Title", "Unknown Journal")
            abstract_section = find_key(article, "Abstract", {})
            abstract_list = find_key(abstract_section, "AbstractText", [])
            abstract = " ".join([str(a) for a in abstract_list]) if isinstance(abstract_list, (list, tuple)) else str(abstract_list)

            # High-impact 저널 여부 확인 (대소문자 구분 없이)
            is_high_impact = any(j.lower() in journal.lower() for j in HIGH_IMPACT_JOURNALS)
            
            papers.append({
                "pmid": pmid, 
                "title": title, 
                "journal": journal, 
                "abstract": abstract, 
                "link": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                "is_high_impact": is_high_impact
            })
        except Exception as e:
            print(f"Error processing PMID {pmid}: {e}")
            continue

    return papers

def gemini_summarize(abstract, title):
    if not abstract.strip() or abstract == "Unknown":
        return "<p>제공된 Abstract가 없습니다.</p>"
    
    # 무료로 사용 가능한 최상위 지능 모델 적용
    model = genai.GenerativeModel('gemini-2.5-flash')
    
    # 임상/학술적 근거와 정확한 사실 기반을 강조한 프롬프트 구성
    prompt = f"""You are a senior clinical researcher evaluating medical literature.
Strictly base your summary ONLY on the provided abstract. The content must be based on clear evidence, accurate facts, and relevant clinical/academic guidelines. Do not hallucinate or add outside information.
Output **ONLY HTML** (no ```html, no markdown blocks).
Use <strong> for bold, <ul><li> for bullet points.
**Exactly 2-3 bullet points only** highlighting the most important translational/clinical findings.

**Language Requirement**: Write the summary sentences in natural, professional **Korean**, but strictly keep key medical, scientific, and anatomical terminology in **English** (e.g., "Spinal cord injury 모델에서...", "Neuroplasticity를 촉진하여...").

Title: {title}
Abstract: {abstract}"""

    # 의학 용어(손상, 질병 등)로 인한 차단을 방지하기 위한 안전 필터 해제
    safety_settings = {
        HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
    }
    
    try:
        response = model.generate_content(
            prompt,
            safety_settings=safety_settings,
            generation_config=genai.GenerationConfig(
                max_output_tokens=600,
                temperature=0.2
            )
        )
        # API 호출 제한(Rate Limit) 방지를 위해 요약 완료 후 5초 휴식
        time.sleep(5)
        return response.text.strip()
    except Exception as e:
        error_msg = str(e)
        print(f"Gemini API Error for '{title[:30]}': {error_msg}")
        return f"<p style='color:red;'>요약 생성 중 오류가 발생했습니다: {error_msg}</p>"

def get_top_papers(all_papers, count=2):
    """High-impact 저널을 우선으로 하여 가장 중요한 논문을 추출합니다."""
    high_impact_papers = [p for p in all_papers if p["is_high_impact"]]
    other_papers = [p for p in all_papers if not p["is_high_impact"]]
    
    # High-impact가 먼저, 그 다음 나머지 논문 순으로 합친 후 필요한 개수만큼 자름
    sorted_papers = high_impact_papers + other_papers
    return sorted_papers[:count]

def format_paper_html(p, index=None):
    summary = gemini_summarize(p["abstract"], p["title"])
    index_str = f"{index}. " if index else ""
    # High-impact 저널인 경우 강조 표시
    journal_str = f"<strong><span style='color:#b30000;'>{p['journal']} (High-Impact)</span></strong>" if p["is_high_impact"] else p['journal']
    
    return f"""
    <div style="margin-bottom: 25px; padding: 15px; border: 1px solid #ddd; border-radius: 5px; background-color: #fafafa;">
        <h4 style="margin-top: 0; color: #2c3e50;">{index_str}{p['title']}</h4>
        <p style="margin: 5px 0; font-size: 0.9em;">
            <strong>Journal:</strong> {journal_str}<br>
            <strong>PMID:</strong> {p['pmid']} | <a href="{p['link']}" target="_blank" style="color: #2980b9;">PubMed 링크</a>
        </p>
        <div style="margin-top: 10px; padding-top: 10px; border-top: 1px dashed #ccc;">
            <strong style="color: #333;">💡 핵심 요약:</strong>
            <div style="margin-top: 5px; color: #444;">{summary}</div>
        </div>
    </div>
"""

def send_email(neural_papers, sarc_papers):
    if not neural_papers and not sarc_papers:
        print("No papers found for today's query.")
        return
        
    all_papers = neural_papers + sarc_papers
    top_papers = get_top_papers(all_papers, count=2)
    
    body_html = f"""<html><body style="font-family: 'Malgun Gothic', dotum, sans-serif; line-height: 1.6; color: #333; max-width: 800px; margin: 0 auto;">
    <h2 style="color: #2c3e50; border-bottom: 2px solid #2c3e50; padding-bottom: 10px;">📊 {yesterday_date} PubMed 최신 동향 리포트</h2>
    <p>연구자님, 지정하신 전문 분야의 최신 논문 검색 결과입니다. 학술적 근거와 명확한 사실에 기반하여 요약되었습니다.</p>
"""

    # 1. 주목할 만한 주요 논문 (Top 1-2) 섹션
    if top_papers:
        body_html += """
    <h3 style="color: #c0392b; margin-top: 30px; padding: 5px 10px; background-color: #fdebd0; border-left: 5px solid #c0392b;">
        🌟 오늘의 주요 논문 Top 2
    </h3>
    <p style="font-size: 0.9em; color: #666;">* High-impact 저널 및 검색 적합도를 우선으로 선정되었습니다.</p>
"""
        for p in top_papers:
            body_html += format_paper_html(p)

    # 2. 신경재생 및 가소성 섹션
    body_html += """
    <h3 style="color: #2980b9; margin-top: 40px; padding: 5px 10px; background-color: #ebf5fb; border-left: 5px solid #2980b9;">
        🧠 신경재생·가소성 섹션 (전체)
    </h3>
"""
    if neural_papers:
        for i, p in enumerate(neural_papers, 1):
            body_html += format_paper_html(p, index=i)
    else:
        body_html += "<p>해당 분야의 새로운 논문이 없습니다.</p>"

    # 3. 사르코페니아 섹션
    body_html += """
    <h3 style="color: #27ae60; margin-top: 40px; padding: 5px 10px; background-color: #e9f7ef; border-left: 5px solid #27ae60;">
        💪 Sarcopenia 섹션 (전체)
    </h3>
"""
    if sarc_papers:
        for i, p in enumerate(sarc_papers, 1):
            body_html += format_paper_html(p, index=i)
    else:
        body_html += "<p>해당 분야의 새로운 논문이 없습니다.</p>"

    body_html += """
    <hr style="margin-top: 40px; border: 0; border-top: 1px solid #eee;">
    <p style="text-align: center; color: #888; font-size: 0.85em;">
        <em>본 이메일은 Gemini API를 활용하여 자동화된 검색 및 요약 결과를 제공합니다.</em>
    </p>
    </body></html>
"""
    
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[{yesterday_date}] 주요 논문 요약: 신경재생 & 사르코페니아 (총 {len(all_papers)}건)"
    msg["From"] = f"Neuro-Sarc Alert <{GMAIL_USER}>"
    msg["To"] = TO_EMAIL
    
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        print("✅ HTML Email sent successfully!")
    except Exception as e:
        print(f"이메일 전송 중 오류 발생: {e}")

if __name__ == "__main__":
    print("=== Neuro-Sarc Daily Alert Script Started ===")
    print(f"Searching papers for date: {yesterday_date}")
    
    neural = fetch_papers(NEURAL_QUERY, max_results=10) # 속도 및 API 제한을 고려해 검색 수 조정
    sarc = fetch_papers(SARC_QUERY, max_results=10)
    
    send_email(neural, sarc)
    print("=== Script completed SUCCESSFULLY ===")

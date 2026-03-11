import os
import datetime
import traceback
from Bio import Entrez
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import google.generativeai as genai  # OpenAI 대신 Google 공식 라이브러리 사용

# 환경 변수 설정
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
TO_EMAIL = os.getenv("TO_EMAIL")
NCBI_EMAIL = os.getenv("NCBI_EMAIL")

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
    print(f"Fetching papers for query (date: {date_str}): {query[:150]}...")
    handle = Entrez.esearch(db="pubmed", term=query, retmax=max_results, sort="pub+date")
    record = Entrez.read(handle)
    handle.close()
    pmids = record.get("IdList", [])
    print(f"Found {len(pmids)} PMIDs")

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

            if any(j.lower() in journal.lower() for j in HIGH_IMPACT_JOURNALS) or len(papers) < 8:
                papers.append({"pmid": pmid, "title": title, "journal": journal, "abstract": abstract, "link": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"})
                print(f"✅ Added: {title[:70]}... ({journal})")
        except Exception as e:
            print(f"Error PMID {pmid}: {e}")
            continue

    print(f"Final papers collected: {len(papers)}")
    return papers[:8]

def gemini_summarize(abstract, category):
    if not abstract.strip():
        return "<p>Abstract 없음.</p>"
    
    # 무료로 가장 빠르고 효율적인 모델 선택
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    prompt = f"""You are a senior clinical researcher in neural regeneration/plasticity and sarcopenia.
Strictly base your summary ONLY on the provided abstract. Ensure high academic accuracy and clear evidence basis.
Output **ONLY HTML** (no ```html, no extra text).
Use <strong> for bold, <ul><li> for bullet points.
**Exactly 2-3 bullet points only** — the most important points only.
Keep it very concise and focused on translational/clinical value for the researcher.
Abstract: {abstract}"""
    
    try:
        response = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                max_output_tokens=500,
                temperature=0.3
            )
        )
        return response.text.strip()
    except Exception as e:
        print(f"Gemini API Error: {e}")
        return "<p>요약 생성 중 오류가 발생했습니다.</p>"

def send_email(neural_papers, sarc_papers):
    if not neural_papers and not sarc_papers:
        print("No papers today")
        return
        
    body_html = f"""<html><body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
    <p>안녕하세요, 연구자님.</p>
    <p><strong>{today.strftime("%Y-%m-%d")}</strong> PubMed 등록 논문 요약입니다.</p>
    <h3>【신경재생·가소성 섹션】</h3>
"""
    for i, p in enumerate(neural_papers, 1):
        summary = gemini_summarize(p["abstract"], "neural regeneration and plasticity")
        body_html += f"""
    <p><strong>{i}. {p['title']}</strong><br>
    Journal: {p['journal']}<br>
    PMID: {p['pmid']}<br>
    Link: <a href="{p['link']}">{p['link']}</a><br>
    요약:<br>
    {summary}</p>
"""
    body_html += "<h3>【사르코페니아 섹션 (SCI 무관)】</h3>"
    for i, p in enumerate(sarc_papers, 1):
        summary = gemini_summarize(p["abstract"], "sarcopenia with drug repositioning, rehabilitation, electrical stimulation")
        body_html += f"""
    <p><strong>{i}. {p['title']}</strong><br>
    Journal: {p['journal']}<br>
    PMID: {p['pmid']}<br>
    Link: <a href="{p['link']}">{p['link']}</a><br>
    요약:<br>
    {summary}</p>
"""
    body_html += "<p><em>총평: Gemini 자동 분석 완료. 연구에 바로 활용하세요.</em></p></body></html>"
    
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[{yesterday_date}] 신경재생·가소성 + 사르코페니아 최신 논문 요약 ({len(neural_papers)+len(sarc_papers)}건)"
    msg["From"] = f"Neuro-Sarc Alert <{GMAIL_USER}>"
    msg["To"] = TO_EMAIL
    
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        print("✅ Concise HTML Email sent successfully!")
    except Exception as e:
        print(f"이메일 전송 중 오류 발생: {e}")

if __name__ == "__main__":
    print("=== Neuro-Sarc Daily Alert Script Started ===")
    print(f"Yesterday date: {yesterday_date}")
    
    neural = fetch_papers(NEURAL_QUERY)
    sarc = fetch_papers(SARC_QUERY)
    
    send_email(neural, sarc)
    print("=== Script completed SUCCESSFULLY ===")

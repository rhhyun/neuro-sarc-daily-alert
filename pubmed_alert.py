import os
import datetime
import traceback
from Bio import Entrez
from openai import OpenAI
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# 환경 변수
XAI_API_KEY = os.getenv("XAI_API_KEY")
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
TO_EMAIL = os.getenv("TO_EMAIL")
NCBI_EMAIL = os.getenv("NCBI_EMAIL")

Entrez.email = NCBI_EMAIL

HIGH_IMPACT_JOURNALS = [
    "Nature", "Science", "Cell", "Nature Neuroscience", "Neuron", "Nature Medicine",
    "Lancet Neurology", "Biomaterials", "Advanced Materials", "J Cachexia Sarcopenia Muscle",
    "Experimental Cell Research", "Journal of Spinal Cord Medicine", "Neural Regeneration Research",
    "Rehabilitation", "Frontiers in Pharmacology", "Nutrients", "Stem Cell Reports"
]

# 날짜 계산
today = datetime.date.today()
yesterday = today - datetime.timedelta(days=1)
date_str = yesterday.strftime("%Y/%m/%d")
yesterday_date = yesterday.strftime("%Y-%m-%d")

# 검색 쿼리 (변경 없음)
NEURAL_QUERY = (
    f'("spinal cord injury"[Title/Abstract] OR "peripheral nerve injury"[Title/Abstract] '
    f'OR electroceutical*[Title/Abstract] OR "drug repositioning"[Title/Abstract] '
    f'OR "gene therapy"[Title/Abstract] OR "biomaterial scaffold"[Title/Abstract] '
    f'OR "neural regeneration"[Title/Abstract] OR "neural plasticity"[Title/Abstract] '
    f'OR "axon regeneration"[Title/Abstract]) AND ("{date_str}"[PDAT] : "{date_str}"[PDAT])'
)

SARC_QUERY = (
    f'("sarcopenia"[Title/Abstract] OR "muscle atrophy"[Title/Abstract] OR "muscle wasting"[Title/Abstract]) '
    f'AND ("drug repositioning"[Title/Abstract] OR repositioning[Title/Abstract] OR rehabilitation[Title/Abstract] '
    f'OR "physical therapy"[Title/Abstract] OR "electrical stimulation"[Title/Abstract] '
    f'OR NMES[Title/Abstract] OR FES[Title/Abstract] OR electrostimulation[Title/Abstract]) '
    f'AND ("{date_str}"[PDAT] : "{date_str}"[PDAT])'
)

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

            # === 핵심 수정: NCBI XML 구조 안전 처리 ===
            if isinstance(raw_record, dict) and "PubmedArticleSet" in raw_record:
                article_set = raw_record["PubmedArticleSet"]
                if isinstance(article_set, list) and len(article_set) > 0:
                    article = article_set[0]
                else:
                    print(f"PMID {pmid}: Empty PubmedArticleSet")
                    continue
            elif isinstance(raw_record, list) and len(raw_record) > 0:
                article = raw_record[0]
            else:
                print(f"PMID {pmid}: Unexpected record type {type(raw_record)}")
                continue

            # MedlineCitation 추출
            medline = article.get("MedlineCitation", {})
            cit = medline.get("Article", {})
            journal = cit.get("Journal", {}).get("Title", "Unknown Journal")
            title = cit.get("ArticleTitle", "No Title")
            abstract_section = cit.get("Abstract", {})
            abstract_list = abstract_section.get("AbstractText", []) if isinstance(abstract_section, dict) else []
            abstract = " ".join([str(a) for a in abstract_list]) if abstract_list else ""

            if any(j.lower() in journal.lower() for j in HIGH_IMPACT_JOURNALS) or len(papers) < 8:
                papers.append({
                    "pmid": pmid,
                    "title": title,
                    "journal": journal,
                    "abstract": abstract,
                    "link": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
                })
                print(f"Added paper: {title[:60]}... ({journal})")
        except Exception as e:
            print(f"Error processing PMID {pmid}: {e}")
            continue

    print(f"Final papers collected: {len(papers)}")
    return papers[:8]

def grok_summarize(abstract, category):
    if not abstract.strip():
        return "• Abstract가 없거나 비공개입니다.\n• 연구자 관점에서 중요 정보 부족."
    client = OpenAI(api_key=XAI_API_KEY, base_url="https://api.x.ai/v1")
    prompt = f"""You are a senior researcher in neural regeneration/plasticity and sarcopenia.
Summarize the following PubMed abstract in **Korean** with exactly 4-5 bullet points.
Focus on: key findings, translational implications for {category}, relation to drug repositioning / rehabilitation / electrical stimulation / gene therapy / scaffolds.
Abstract: {abstract}"""
    response = client.chat.completions.create(
        model="grok-4",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=600,
        temperature=0.3
    )
    return response.choices[0].message.content.strip()

def send_email(neural_papers, sarc_papers):
    if not neural_papers and not sarc_papers:
        print("No papers today - skipping email")
        return

    body = f"""안녕하세요, 연구자님.

{today.strftime("%Y-%m-%d")} PubMed 등록 논문 중 
신경재생·가소성 {len(neural_papers)}건 + 사르코페니아(독립·리포지셔닝·재활·전기자극) {len(sarc_papers)}건을 선별했습니다.

### 【신경재생·가소성 섹션】
"""
    for i, p in enumerate(neural_papers, 1):
        summary = grok_summarize(p["abstract"], "neural regeneration and plasticity")
        body += f"**{i}. {p['title']}**\nJournal: {p['journal']}\nPMID: {p['pmid']}\nLink: {p['link']}\n요약:\n{summary}\n\n"

    body += "### 【사르코페니아 섹션 (SCI 무관)】\n"
    for i, p in enumerate(sarc_papers, 1):
        summary = grok_summarize(p["abstract"], "sarcopenia with drug repositioning, rehabilitation, electrical stimulation")
        body += f"**{i}. {p['title']}**\nJournal: {p['journal']}\nPMID: {p['pmid']}\nLink: {p['link']}\n요약:\n{summary}\n\n"

    body += "총평: Grok-4 자동 분석 완료. 연구에 바로 활용하세요."

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[{yesterday_date}] 신경재생·가소성 + 사르코페니아 논문 요약 ({len(neural_papers)+len(sarc_papers)}건)"
    msg["From"] = GMAIL_USER
    msg["To"] = TO_EMAIL
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.send_message(msg)
    print("✅ Email sent successfully!")

if __name__ == "__main__":
    print("=== Neuro-Sarc Daily Alert Script Started ===")
    print(f"Yesterday date: {yesterday_date}")
    try:
        neural = fetch_papers(NEURAL_QUERY)
        sarc = fetch_papers(SARC_QUERY)
        send_email(neural, sarc)
        print("=== Script completed SUCCESSFULLY ===")
    except Exception as e:
        print("=== CRITICAL ERROR ===")
        print(str(e))
        traceback.print_exc()
        raise

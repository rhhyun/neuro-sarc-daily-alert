#!/usr/bin/env python3
"""
PubMed 자동 알림 스크립트 v2.0
- 신경재생·가소성, 사르코페니아, 의료AI·뇌자극 분야 최신 논문
- 매주 월/수/토 실행 (GitHub Actions 스케줄)
- Gemini 2.5 Flash 한국어 요약, Gmail 발송

수정 이력 (v2.0):
  - 중복 API 호출 방지 (PMID 기반 summary cache)
  - 저널 고영향력 판별 로직 오탐 수정 (단어 경계 처리)
  - Gemini 서두 문구 자동 제거 (정규식)
  - 마크다운 볼드체 완전 제거
  - 월/수/토 스케줄 맞춤 동적 날짜 범위
  - 의료AI·뇌전기자극 3번째 섹션 추가
  - Top 3 논문은 아래 섹션에서 중복 표시 안 됨
  - 지수 백오프 Rate limit 재시도 로직
"""

import os
import re
import time
import datetime
from typing import Optional

from Bio import Entrez
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold


# ═══════════════════════════════════════════════════════════
# 1. 환경 변수 & 초기화
# ═══════════════════════════════════════════════════════════
GEMINI_API_KEY = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
GMAIL_USER     = os.getenv("GMAIL_USER", "")
GMAIL_APP_PW   = os.getenv("GMAIL_APP_PASSWORD", "")
TO_EMAIL       = os.getenv("TO_EMAIL", "")
NCBI_EMAIL     = os.getenv("NCBI_EMAIL", "user@example.com")

if not GEMINI_API_KEY:
    raise SystemExit("GEMINI_API_KEY 환경 변수가 없습니다. GitHub Secrets를 확인하세요.")

genai.configure(api_key=GEMINI_API_KEY)
Entrez.email = NCBI_EMAIL


# ═══════════════════════════════════════════════════════════
# 2. 고영향력 저널 판별 (IF >= 9 또는 Q1 기준)
#
#   오탐 방지:
#     - "nature"    → "nature" / "nature medicine" 등만 OK,
#                     "Frontiers in Nature" 등 제외
#     - "cell"      → "Cell" / "Cell Reports Medicine" 등만 OK,
#                     "Stem cells (Dayton)" 제외
#     - "science"   → "Science" / "Science Advances" 등만 OK,
#                     "Journal of Neuroscience" 제외
#     - "brain"     → "Brain" (Oxford, IF~14) 만 OK,
#                     "Brain Research" (IF~3) 제외
# ═══════════════════════════════════════════════════════════

# 저널명이 이 단어로 시작하거나 정확히 일치해야 high-impact로 인정
_EXACT_START = {
    "nature", "science", "cell", "brain", "neuron", "lancet",
}

# 저널명에 이 문자열이 포함되면 high-impact
_SUBSTR_MATCH = [
    # Multidisciplinary / General
    "new england journal",
    "jama",
    # Neurology / Neuroscience
    "nature neuroscience",
    "nature communications",
    "nature medicine",
    "nature methods",
    "nature biotechnology",
    "lancet neurology",
    "lancet oncology",
    "lancet digital health",
    "jama neurology",
    "annals of neurology",
    "brain stimulation",          # Brain Stimulation (IF~9)
    # Translational Medicine
    "science translational medicine",
    "science advances",
    "science immunology",
    "cell reports medicine",
    "cell stem cell",
    "signal transduction and targeted therapy",
    "theranostics",
    # Biomaterials / Engineering
    "advanced materials",
    "bioactive materials",
    "advanced healthcare materials",
    "advanced science",
    "acta biomaterialia",
    "biomaterials",
    # Specific fields
    "journal of cachexia",
    "npj regenerative medicine",
    "neural regeneration research",
    "stem cell reports",
    "autophagy",
    "redox biology",
    "elife",
    "plos biology",
]


def is_high_impact(journal: str) -> bool:
    """저널명을 받아 고영향력 여부 반환 (IF >= 9 / Q1 기준)"""
    j = journal.lower().strip()
    # 짧은 이름: 정확히 일치하거나 그 단어로 시작하는 경우만 허용
    for name in _EXACT_START:
        if j == name or j.startswith(name + " ") or j.startswith(name + ":"):
            return True
    # 긴 이름: 포함 여부
    return any(sub in j for sub in _SUBSTR_MATCH)


# ═══════════════════════════════════════════════════════════
# 3. 검색 날짜 범위 (월/수/토 스케줄 자동 설정)
# ═══════════════════════════════════════════════════════════

def get_date_range():
    """
    실행 요일에 따라 검색 기간 자동 설정
      - 월요일(0): 금·토·일 (3일)
      - 수요일(2): 월·화    (2일)
      - 토요일(5): 수·목·금 (3일)
      - 그 외     : 전날    (1일)
    """
    today    = datetime.date.today()
    days_back = {0: 3, 2: 2, 5: 3}.get(today.weekday(), 1)
    start    = today - datetime.timedelta(days=days_back)
    end      = today - datetime.timedelta(days=1)
    return start, end


_START_DT, _END_DT = get_date_range()
DATE_RANGE_QUERY   = (
    f'("{_START_DT.strftime("%Y/%m/%d")}"[PDAT]'
    f' : "{_END_DT.strftime("%Y/%m/%d")}"[PDAT])'
)
DATE_LABEL = (
    f"{_START_DT.strftime('%Y-%m-%d')} ~ {_END_DT.strftime('%Y-%m-%d')}"
)


# ═══════════════════════════════════════════════════════════
# 4. PubMed 검색 쿼리 3종
# ═══════════════════════════════════════════════════════════

NEURAL_QUERY = f"""(
    "spinal cord injury"[Title/Abstract]        OR
    "peripheral nerve injury"[Title/Abstract]   OR
    "neural regeneration"[Title/Abstract]       OR
    "neuroplasticity"[Title/Abstract]           OR
    "axon regeneration"[Title/Abstract]         OR
    "neuroprotection"[Title/Abstract]           OR
    "electroceutical"[Title/Abstract]           OR
    "epidural stimulation"[Title/Abstract]      OR
    "functional electrical stimulation"[Title/Abstract] OR
    "transcutaneous electrical stimulation"[Title/Abstract] OR
    "hydrogel"[Title/Abstract]                  OR
    "biomaterial scaffold"[Title/Abstract]      OR
    "drug repositioning"[Title/Abstract]        OR
    "gene therapy"[Title/Abstract]
) AND {DATE_RANGE_QUERY}"""

SARC_QUERY = f"""(
    "sarcopenia"[Title/Abstract]     OR
    "muscle atrophy"[Title/Abstract] OR
    "muscle wasting"[Title/Abstract] OR
    "cachexia"[Title/Abstract]
) AND (
    "treatment"[Title/Abstract]           OR
    "therapy"[Title/Abstract]             OR
    "electrical stimulation"[Title/Abstract] OR
    "NMES"[Title/Abstract]                OR
    "FES"[Title/Abstract]                 OR
    "rehabilitation"[Title/Abstract]      OR
    "muscle hypertrophy"[Title/Abstract]  OR
    "functional recovery"[Title/Abstract] OR
    "repositioning"[Title/Abstract]       OR
    "drug"[Title/Abstract]
) AND {DATE_RANGE_QUERY}"""

OTHER_QUERY = f"""(
    (
        (
            "artificial intelligence"[Title/Abstract]   OR
            "machine learning"[Title/Abstract]          OR
            "deep learning"[Title/Abstract]             OR
            "large language model"[Title/Abstract]
        ) AND (
            "spinal cord"[Title/Abstract]        OR
            "sarcopenia"[Title/Abstract]         OR
            "neurology"[Title/Abstract]          OR
            "rehabilitation"[Title/Abstract]     OR
            "muscle"[Title/Abstract]             OR
            "neurorehabilitation"[Title/Abstract]
        )
    ) OR (
        "brain stimulation"[Title/Abstract]                      OR
        "transcranial magnetic stimulation"[Title/Abstract]      OR
        "transcranial direct current stimulation"[Title/Abstract] OR
        "deep brain stimulation"[Title/Abstract]                 OR
        "brain-computer interface"[Title/Abstract]               OR
        "neural interface"[Title/Abstract]                       OR
        "electrocorticography"[Title/Abstract]
    )
) AND {DATE_RANGE_QUERY}"""


# ═══════════════════════════════════════════════════════════
# 5. PubMed 논문 수집
# ═══════════════════════════════════════════════════════════

def _find_key(obj, key: str, default=""):
    """중첩 dict/list에서 key를 재귀적으로 탐색"""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _find_key(v, key, default)
            if r != default:
                return r
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            r = _find_key(item, key, default)
            if r != default:
                return r
    return default


def fetch_papers(query: str, max_results: int = 10) -> list:
    print(f"  PubMed 검색 중 (최대 {max_results}건)...")
    try:
        handle = Entrez.esearch(
            db="pubmed", term=query, retmax=max_results, sort="relevance"
        )
        record = Entrez.read(handle)
        handle.close()
        pmids = record.get("IdList", [])
    except Exception as e:
        print(f"  검색 오류: {e}")
        return []

    papers = []
    for pmid in pmids:
        try:
            handle = Entrez.efetch(db="pubmed", id=pmid, retmode="xml")
            raw = Entrez.read(handle)
            handle.close()

            # 레코드 언팩
            article = raw
            if isinstance(article, dict) and "PubmedArticleSet" in article:
                article = article["PubmedArticleSet"]
            if isinstance(article, (list, tuple)) and article:
                article = article[0]

            title   = str(_find_key(article, "ArticleTitle", "No Title"))
            journal = str(_find_key(article, "Title",        "Unknown Journal"))

            ab_obj  = _find_key(article, "Abstract", {})
            ab_text = _find_key(ab_obj, "AbstractText", "")
            if isinstance(ab_text, (list, tuple)):
                abstract = " ".join(str(a) for a in ab_text).strip()
            else:
                abstract = str(ab_text).strip()

            papers.append({
                "pmid":           pmid,
                "title":          title,
                "journal":        journal,
                "abstract":       abstract,
                "link":           f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                "is_high_impact": is_high_impact(journal),
            })
            time.sleep(0.4)   # NCBI 속도 제한: API 키 없을 때 3 req/sec
        except Exception as e:
            print(f"  PMID {pmid} 처리 오류: {e}")

    print(f"  {len(papers)}건 수집 완료")
    return papers


# ═══════════════════════════════════════════════════════════
# 6. Gemini 2.5 Flash 요약 (캐시 + Rate limit 재시도)
# ═══════════════════════════════════════════════════════════

_summary_cache: dict = {}   # PMID → HTML 요약 문자열

_GEMINI_MODEL = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    safety_settings={
        HarmCategory.HARM_CATEGORY_HARASSMENT:        HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_HATE_SPEECH:       HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
    },
    generation_config=genai.GenerationConfig(
        max_output_tokens=700,
        temperature=0.1,
    ),
)

_PROMPT_TEMPLATE = """\
당신은 의과학 연구자를 위한 논문 요약 전문가입니다.

[필수 출력 규칙 - 반드시 준수]
1. 첫 줄부터 바로 "- "로 시작하는 불릿 포인트를 쓰십시오.
2. "다음은...", "아래는...", "요약:" 같은 서두 문구를 절대 쓰지 마십시오.
3. 불릿 포인트 2~3개만 작성하십시오.
4. 각 불릿은 반드시 완전한 문장으로 끝내십시오 (절대 중간에 잘라내지 마십시오).
5. 마크다운 기호(**, *, #, `) 절대 사용 금지.
6. "<" 는 "미만", ">" 는 "초과" 로 표기하십시오.
7. 한국어로 작성하되 의학·과학 전문 용어는 영어 그대로 유지하십시오.

[논문]
제목: {title}
초록: {abstract}
"""


def gemini_summarize(pmid: str, abstract: str, title: str) -> str:
    """PMID별 Gemini 요약 생성 (캐시로 중복 호출 방지)"""
    if pmid in _summary_cache:
        return _summary_cache[pmid]

    if not abstract or not abstract.strip():
        result = "PubMed에 등록된 Abstract가 없습니다."
        _summary_cache[pmid] = result
        return result

    prompt = _PROMPT_TEMPLATE.format(
        title=title,
        abstract=abstract[:3000],
    )

    for attempt in range(5):
        try:
            response = _GEMINI_MODEL.generate_content(prompt)
            raw = response.text.strip()

            # ── 후처리 정제 ──────────────────────────────
            # 1) 서두 문구 제거 (Gemini가 무시할 경우를 대비)
            raw = re.sub(
                r"^(다음은|아래는|요약:|이하는)[^\n]*\n+",
                "", raw, flags=re.MULTILINE | re.IGNORECASE
            ).strip()
            # 2) 마크다운 볼드·이탤릭 제거
            raw = re.sub(r"\*\*(.+?)\*\*", r"\1", raw)
            raw = re.sub(r"\*(.+?)\*",     r"\1", raw)
            # 3) 부등호 → 한국어
            raw = raw.replace("<", " 미만 ").replace(">", " 초과 ")
            # 4) 줄바꿈 → HTML
            html_text = raw.replace("\n", "<br>")
            # ─────────────────────────────────────────────

            _summary_cache[pmid] = html_text
            print(f"    PMID {pmid} 요약 완료 → 15초 대기")
            time.sleep(15)   # ~4 RPM 유지 (무료 10 RPM 이내)
            return html_text

        except Exception as e:
            err = str(e)
            if "429" in err or "quota" in err.lower() or "resource" in err.lower():
                wait = 70 * (attempt + 1)
                print(f"    Rate limit 초과. {wait}초 대기 후 재시도 ({attempt+1}/5)...")
                time.sleep(wait)
            else:
                result = f"요약 생성 오류: {err[:150]}"
                _summary_cache[pmid] = result
                return result

    result = "API 호출 한도 초과로 요약을 생성하지 못했습니다."
    _summary_cache[pmid] = result
    return result


# ═══════════════════════════════════════════════════════════
# 7. HTML 카드 & 섹션 빌더
# ═══════════════════════════════════════════════════════════

def paper_card_html(
    paper: dict,
    index: Optional[int] = None,
    is_top: bool = False,
) -> str:
    """논문 1건의 HTML 카드 반환 (요약 포함)"""
    summary = gemini_summarize(paper["pmid"], paper["abstract"], paper["title"])
    idx_str = f"{index}. " if index is not None else ""

    border = "#c0392b" if is_top else ("#2980b9" if paper["is_high_impact"] else "#95a5a6")

    if paper["is_high_impact"]:
        journal_html = (
            f"<span style='color:#c0392b; font-weight:bold;'>{paper['journal']}</span>"
            f"&nbsp;<span style='color:#c0392b;'>★ High-Impact</span>"
        )
    else:
        journal_html = paper["journal"]

    return f"""
<div style="margin-bottom:18px; padding:14px 16px;
            border-left:4px solid {border}; border-radius:4px;
            background:#fafafa; box-shadow:0 1px 3px rgba(0,0,0,0.07);">
  <p style="margin:0 0 5px 0; font-weight:bold; font-size:0.97em;
            color:#2c3e50; line-height:1.4;">
    {idx_str}{paper['title']}
  </p>
  <p style="margin:0 0 8px 0; font-size:0.83em; color:#666;">
    <b>저널:</b> {journal_html} &nbsp;&#124;&nbsp;
    <b>PMID:</b> {paper['pmid']} &nbsp;&#124;&nbsp;
    <a href="{paper['link']}" target="_blank"
       style="color:#2980b9; text-decoration:none;">PubMed &#8599;</a>
  </p>
  <div style="font-size:0.9em; color:#333; line-height:1.85;
              border-top:1px dashed #ddd; padding-top:8px;">
    <b style="color:#555;">핵심 요약:</b><br>
    {summary}
  </div>
</div>
"""


def build_section(
    title_text:   str,
    color:        str,
    bg_color:     str,
    papers:       list,
    exclude_pmids: set,
) -> str:
    """섹션 HTML 반환. exclude_pmids에 있는 논문은 건너뜀."""
    html = (
        f'<h3 style="color:{color}; margin-top:32px; padding:6px 14px;'
        f' background:{bg_color}; border-left:5px solid {color};'
        f' border-radius:2px;">{title_text}</h3>\n'
    )
    visible = [p for p in papers if p["pmid"] not in exclude_pmids]
    if not visible:
        html += '<p style="color:#999; font-size:0.9em;">해당 기간 새로운 논문이 없습니다.</p>\n'
    else:
        for i, p in enumerate(visible, 1):
            html += paper_card_html(p, index=i)
    return html


# ═══════════════════════════════════════════════════════════
# 8. 이메일 빌드 & 발송
# ═══════════════════════════════════════════════════════════

def build_and_send_email(
    neural: list,
    sarc:   list,
    other:  list,
) -> None:
    # ── 전체 고유 논문 집합 ──────────────────────────────
    seen: set   = set()
    unique_all: list = []
    for p in neural + sarc + other:
        if p["pmid"] not in seen:
            unique_all.append(p)
            seen.add(p["pmid"])

    if not unique_all:
        print("검색된 논문이 없어 이메일을 발송하지 않습니다.")
        return

    total = len(unique_all)

    # ── Top 3 선정: high-impact 우선, PMID 역순(최신) ────
    top3 = sorted(
        unique_all,
        key=lambda p: (0 if p["is_high_impact"] else 1, -int(p["pmid"]))
    )[:3]
    top3_pmids = {p["pmid"] for p in top3}

    # ── HTML 헤더 ────────────────────────────────────────
    html = f"""
<html><head><meta charset="utf-8"></head>
<body style="font-family:'Malgun Gothic',dotum,Arial,sans-serif;
             line-height:1.6; color:#333;
             max-width:820px; margin:0 auto; padding:20px;">

<!-- 상단 배너 -->
<div style="background:linear-gradient(135deg,#1a252f 0%,#2980b9 100%);
            color:white; padding:22px 24px; border-radius:8px; margin-bottom:22px;">
  <h2 style="margin:0 0 6px 0; font-size:1.35em;">
    &#128202; PubMed 연구 동향 리포트
  </h2>
  <p style="margin:0; opacity:0.85; font-size:0.88em;">
    검색 기간: <strong>{DATE_LABEL}</strong>
    &nbsp;|&nbsp; 총 <strong>{total}건</strong> 논문 검색
  </p>
</div>

<p style="color:#666; font-size:0.88em; margin-top:0;">
  신경재생·가소성, 사르코페니아, 의료AI·뇌자극 분야 최신 논문 자동 요약 리포트입니다.<br>
  <span style="color:#c0392b;">&#9733; High-Impact</span>
  = SCIE Q1 또는 IF &ge; 9 저널.
</p>
"""

    # ── Top 3 섹션 ───────────────────────────────────────
    html += """
<h3 style="color:#c0392b; margin-top:20px; padding:6px 14px;
           background:#fdebd0; border-left:5px solid #c0392b; border-radius:2px;">
  &#127775; 이번 기간 주요 논문 Top 3
</h3>
<p style="font-size:0.83em; color:#999; margin-top:0;">
  * High-impact 저널 우선 선정. 해당 논문은 아래 각 섹션에서 제외됩니다.
</p>
"""
    for p in top3:
        html += paper_card_html(p, is_top=True)

    # ── 전문 섹션 3종 ────────────────────────────────────
    html += build_section(
        "&#129504; 신경재생·가소성·척수손상 섹션",
        "#2980b9", "#ebf5fb",
        neural, top3_pmids,
    )
    html += build_section(
        "&#128170; 사르코페니아·근감소증 섹션",
        "#27ae60", "#e9f7ef",
        sarc, top3_pmids,
    )
    html += build_section(
        "&#129302; 의료AI·뇌전기자극 섹션",
        "#8e44ad", "#f5eef8",
        other, top3_pmids,
    )

    # ── 푸터 ────────────────────────────────────────────
    html += """
<hr style="margin-top:40px; border:0; border-top:1px solid #eee;">
<p style="text-align:center; color:#bbb; font-size:0.78em; margin-top:12px;">
  <em>Gemini 2.5 Flash + PubMed API 자동 생성 | rhhyun@gmail.com</em>
</p>
</body></html>
"""

    # ── 이메일 전송 ──────────────────────────────────────
    msg = MIMEMultipart("alternative")
    msg["Subject"] = (
        f"[PubMed {DATE_LABEL}] "
        f"신경재생·사르코페니아·의료AI 논문 동향 ({total}건)"
    )
    msg["From"] = f"Neuro-Sarc Research Alert <{GMAIL_USER}>"
    msg["To"]   = TO_EMAIL
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.ehlo()
            server.starttls()
            server.login(GMAIL_USER, GMAIL_APP_PW)
            server.send_message(msg)
        print(f"이메일 전송 완료 → {TO_EMAIL}")
    except Exception as e:
        print(f"이메일 전송 실패: {e}")


# ═══════════════════════════════════════════════════════════
# 9. 메인
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("  PubMed 자동 알림 스크립트 v2.0")
    print(f"  검색 기간: {DATE_LABEL}")
    print("=" * 60)

    print("\n[1/3] 신경재생·가소성·척수손상 논문 검색...")
    neural_papers = fetch_papers(NEURAL_QUERY, max_results=10)

    print("\n[2/3] 사르코페니아·근감소증 논문 검색...")
    sarc_papers   = fetch_papers(SARC_QUERY,   max_results=10)

    print("\n[3/3] 의료AI·뇌자극 논문 검색...")
    other_papers  = fetch_papers(OTHER_QUERY,  max_results=8)

    print(
        f"\n수집 완료: 신경재생 {len(neural_papers)}건 | "
        f"사르코페니아 {len(sarc_papers)}건 | "
        f"의료AI·뇌자극 {len(other_papers)}건"
    )
    print("Gemini 요약 및 이메일 생성 중...\n")

    build_and_send_email(neural_papers, sarc_papers, other_papers)
    print("\n=== 스크립트 완료 ===")

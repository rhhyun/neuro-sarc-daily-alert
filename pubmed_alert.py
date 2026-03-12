diff --git a/pubmed_alert.py b/pubmed_alert.py
index 77cbdc6c38e13630c8b328d485bfcdf4e1e14b8c..438f8a2e2048b8b014941d3d78d70d3647468bcd 100644
--- a/pubmed_alert.py
+++ b/pubmed_alert.py
@@ -1,270 +1,383 @@
-import os
-import time
 import datetime
-import traceback
-from Bio import Entrez
+import html
+import os
+import re
 import smtplib
+import time
 from email.mime.multipart import MIMEMultipart
 from email.mime.text import MIMEText
-import google.generativeai as genai
-from google.generativeai.types import HarmCategory, HarmBlockThreshold
 
-# 1. 환경 변수 설정
-GEMINI_API_KEY = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
+from Bio import Entrez
+from openai import OpenAI
+
+# =========================
+# Environment configuration
+# =========================
+OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
 GMAIL_USER = os.getenv("GMAIL_USER")
 GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
-TO_EMAIL = os.getenv("TO_EMAIL")
+TO_EMAIL = os.getenv("TO_EMAIL", "rhhyun@gmail.com")
 NCBI_EMAIL = os.getenv("NCBI_EMAIL")
 
-if not GEMINI_API_KEY:
-    raise ValueError("🚨 치명적 에러: API 키를 찾을 수 없습니다! GitHub Repository secrets에 키가 정확히 등록되었는지 확인하세요.")
+MAX_RESULTS_NEURAL = int(os.getenv("MAX_RESULTS_NEURAL", "5"))
+MAX_RESULTS_SARC = int(os.getenv("MAX_RESULTS_SARC", "5"))
+MAX_RESULTS_AI = int(os.getenv("MAX_RESULTS_AI", "3"))
+MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
+SUMMARY_DELAY_SECONDS = int(os.getenv("SUMMARY_DELAY_SECONDS", "12"))
+SHOULD_ENFORCE_SCHEDULE = os.getenv("ENFORCE_MWS_SCHEDULE", "false").lower() == "true"
+
+REQUIRED_ENV = {
+    "OPENAI_API_KEY": OPENAI_API_KEY,
+    "GMAIL_USER": GMAIL_USER,
+    "GMAIL_APP_PASSWORD": GMAIL_APP_PASSWORD,
+    "NCBI_EMAIL": NCBI_EMAIL,
+}
 
-# Gemini API 및 Entrez 초기화
-genai.configure(api_key=GEMINI_API_KEY)
+missing = [key for key, value in REQUIRED_ENV.items() if not value]
+if missing:
+    raise ValueError(f"🚨 필수 환경 변수가 누락되었습니다: {', '.join(missing)}")
+
+client = OpenAI(api_key=OPENAI_API_KEY)
 Entrez.email = NCBI_EMAIL
 
 HIGH_IMPACT_JOURNALS = [
-    "Nature", "Science", "Cell", "Nature Neuroscience", "Neuron", "Nature Medicine",
-    "Lancet Neurology", "Biomaterials", "Advanced Materials", "J Cachexia Sarcopenia Muscle",
-    "Experimental Cell Research", "Journal of Spinal Cord Medicine", "Neural Regeneration Research",
-    "Rehabilitation", "Frontiers in Pharmacology", "Nutrients", "Stem Cell Reports"
+    "Nature",
+    "Science",
+    "Cell",
+    "Nature Neuroscience",
+    "Neuron",
+    "Nature Medicine",
+    "Lancet Neurology",
+    "Biomaterials",
+    "Advanced Materials",
+    "J Cachexia Sarcopenia Muscle",
+    "Stem Cell Reports",
+    "Clinical Rehabilitation",
 ]
 
 today = datetime.date.today()
 yesterday = today - datetime.timedelta(days=1)
 date_str = yesterday.strftime("%Y/%m/%d")
 yesterday_date = yesterday.strftime("%Y-%m-%d")
 
-NEURAL_QUERY = f'("spinal cord injury"[Title/Abstract] OR "peripheral nerve injury"[Title/Abstract] OR electroceutical*[Title/Abstract] OR "drug repositioning"[Title/Abstract] OR "gene therapy"[Title/Abstract] OR "biomaterial scaffold"[Title/Abstract] OR "neural regeneration"[Title/Abstract] OR "neural plasticity"[Title/Abstract] OR "axon regeneration"[Title/Abstract]) AND ("{date_str}"[PDAT] : "{date_str}"[PDAT])'
+NEURAL_QUERY = (
+    f'("spinal cord injury"[Title/Abstract] OR "peripheral nerve injury"[Title/Abstract] '
+    f'OR electroceutical*[Title/Abstract] OR "drug repositioning"[Title/Abstract] '
+    f'OR "gene therapy"[Title/Abstract] OR "biomaterial scaffold"[Title/Abstract] '
+    f'OR "neural regeneration"[Title/Abstract] OR "neural plasticity"[Title/Abstract] '
+    f'OR "axon regeneration"[Title/Abstract] OR "electrical stimulation"[Title/Abstract] '
+    f'OR "AI"[Title/Abstract] OR "robot"[Title/Abstract]) '
+    f'AND ("{date_str}"[PDAT] : "{date_str}"[PDAT])'
+)
+
+SARC_QUERY = (
+    f'("sarcopenia"[Title/Abstract] OR "muscle atrophy"[Title/Abstract] OR "muscle wasting"[Title/Abstract] '
+    f'OR cachexia[Title/Abstract]) AND ("drug repositioning"[Title/Abstract] OR repositioning[Title/Abstract] '
+    f'OR rehabilitation[Title/Abstract] OR "physical therapy"[Title/Abstract] OR "electrical stimulation"[Title/Abstract] '
+    f'OR NMES[Title/Abstract] OR FES[Title/Abstract] OR electrostimulation[Title/Abstract] OR AI[Title/Abstract] '
+    f'OR robot[Title/Abstract]) AND ("{date_str}"[PDAT] : "{date_str}"[PDAT])'
+)
+
+AI_QUERY = (
+    f'(("medical AI"[Title/Abstract] OR "artificial intelligence"[Title/Abstract]) '
+    f'AND ("spinal cord"[Title/Abstract] OR "sarcopenia"[Title/Abstract] OR brain[Title/Abstract] '
+    f'OR "electrical stimulation"[Title/Abstract] OR "signal detection"[Title/Abstract])) '
+    f'AND ("{date_str}"[PDAT] : "{date_str}"[PDAT])'
+)
+
+
+def should_run_today() -> bool:
+    if not SHOULD_ENFORCE_SCHEDULE:
+        return True
+    return today.weekday() in {0, 2, 5}
 
-SARC_QUERY = f'("sarcopenia"[Title/Abstract] OR "muscle atrophy"[Title/Abstract] OR "muscle wasting"[Title/Abstract]) AND ("drug repositioning"[Title/Abstract] OR repositioning[Title/Abstract] OR rehabilitation[Title/Abstract] OR "physical therapy"[Title/Abstract] OR "electrical stimulation"[Title/Abstract] OR NMES[Title/Abstract] OR FES[Title/Abstract] OR electrostimulation[Title/Abstract]) AND ("{date_str}"[PDAT] : "{date_str}"[PDAT])'
 
 def find_key(obj, key, default="Unknown"):
     if isinstance(obj, dict):
-        if key in obj: return obj[key]
-        for v in obj.values():
-            result = find_key(v, key, default)
-            if result != default: return result
+        if key in obj:
+            return obj[key]
+        for value in obj.values():
+            result = find_key(value, key, default)
+            if result != default:
+                return result
     elif isinstance(obj, (list, tuple)):
         for item in obj:
             result = find_key(item, key, default)
-            if result != default: return result
+            if result != default:
+                return result
     return default
 
-def fetch_papers(query, max_results=30):
-    print(f"Fetching papers... Query snippet: {query[:100]}...")
+
+def clean_text(value: str) -> str:
+    text = str(value or "").strip()
+    return re.sub(r"\s+", " ", text)
+
+
+def fetch_papers(query, topic, max_results=30):
+    print(f"Fetching papers for {topic}... Query snippet: {query[:90]}...")
     try:
         handle = Entrez.esearch(db="pubmed", term=query, retmax=max_results, sort="pub+date")
         record = Entrez.read(handle)
         handle.close()
         pmids = record.get("IdList", [])
-    except Exception as e:
-        print(f"Entrez search error: {e}")
+    except Exception as error:
+        print(f"Entrez search error ({topic}): {error}")
         return []
 
     papers = []
     for pmid in pmids:
         try:
             handle = Entrez.efetch(db="pubmed", id=pmid, retmode="xml")
             raw_record = Entrez.read(handle)
             handle.close()
 
-            if hasattr(raw_record, 'keys'): raw_record = dict(raw_record)
+            if hasattr(raw_record, "keys"):
+                raw_record = dict(raw_record)
             if isinstance(raw_record, dict) and "PubmedArticleSet" in raw_record:
                 article_set = raw_record["PubmedArticleSet"]
                 article = article_set[0] if isinstance(article_set, (list, tuple)) and article_set else article_set
             else:
                 article = raw_record
-            if hasattr(article, 'keys'): article = dict(article)
 
-            title = find_key(article, "ArticleTitle", "No Title")
-            journal = find_key(article, "Title", "Unknown Journal")
+            if hasattr(article, "keys"):
+                article = dict(article)
+
+            title = clean_text(find_key(article, "ArticleTitle", "No Title"))
+            journal = clean_text(find_key(article, "Title", "Unknown Journal"))
             abstract_section = find_key(article, "Abstract", {})
             abstract_list = find_key(abstract_section, "AbstractText", [])
-            abstract = " ".join([str(a) for a in abstract_list]) if isinstance(abstract_list, (list, tuple)) else str(abstract_list)
+            abstract = (
+                " ".join(clean_text(part) for part in abstract_list)
+                if isinstance(abstract_list, (list, tuple))
+                else clean_text(abstract_list)
+            )
 
             is_high_impact = any(j.lower() in journal.lower() for j in HIGH_IMPACT_JOURNALS)
-            
-            papers.append({
-                "pmid": pmid, 
-                "title": title, 
-                "journal": journal, 
-                "abstract": abstract, 
-                "link": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
-                "is_high_impact": is_high_impact
-            })
-        except Exception as e:
-            print(f"Error processing PMID {pmid}: {e}")
-            continue
 
+            papers.append(
+                {
+                    "pmid": pmid,
+                    "title": title,
+                    "journal": journal,
+                    "abstract": abstract,
+                    "link": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
+                    "topic": topic,
+                    "is_high_impact": is_high_impact,
+                }
+            )
+        except Exception as error:
+            print(f"Error processing PMID {pmid}: {error}")
     return papers
 
-def gemini_summarize(abstract, title):
+
+def simple_fallback_summary(abstract: str) -> str:
+    sentences = re.split(r"(?<=[.!?])\s+", abstract)
+    selected = [s.strip() for s in sentences if len(s.strip()) > 40][:2]
+    if not selected:
+        return "- 핵심 내용 추출이 제한되었습니다. PubMed 링크에서 원문 초록을 확인해 주세요."
+    return "\n".join(f"- {s}" for s in selected)
+
+
+def sanitize_summary(raw_summary: str) -> str:
+    text = raw_summary or ""
+    text = text.replace("<", "미만").replace(">", "초과")
+    text = text.replace("**", "").replace("*", "")
+    text = re.sub(r"^\s*(다음은 제공된 초록.*?요약입니다\.?|제공된 초록을 바탕으로 한 요약입니다\.?)\s*", "", text, flags=re.I)
+    lines = [line.strip() for line in text.splitlines() if line.strip()]
+
+    bullet_lines = []
+    for line in lines:
+        line = re.sub(r"^[•·]\s*", "- ", line)
+        if not line.startswith("-"):
+            line = f"- {line}"
+        bullet_lines.append(line)
+
+    bullet_lines = bullet_lines[:3] if bullet_lines else ["- 요약 결과가 비어 있습니다."]
+    return "\n".join(bullet_lines)
+
+
+def summary_to_html(summary_text: str) -> str:
+    lines = [line.strip() for line in summary_text.splitlines() if line.strip()]
+    items = []
+    for line in lines:
+        content = line[1:].strip() if line.startswith("-") else line
+        items.append(f"<li>{html.escape(content)}</li>")
+    return "<ul style='margin:6px 0 0 20px; padding:0;'>" + "".join(items) + "</ul>"
+
+
+def openai_summarize(abstract: str, title: str) -> str:
     if not abstract.strip() or abstract == "Unknown":
-        return "<p>제공된 Abstract가 없습니다.</p>"
-    
-    # 2.5 Flash 모델 적용
-    model = genai.GenerativeModel('gemini-2.5-flash')
-    
-    prompt = f"""You are a senior clinical researcher evaluating medical literature.
-Strictly base your summary ONLY on the provided abstract.
-CRITICAL INSTRUCTION: Output ONLY plain text. DO NOT use bold (**), DO NOT use asterisks (*), DO NOT use HTML, and NEVER use '<' or '>' symbols. 
-Use a simple hyphen (-) for bullet points.
-Exactly 2-3 bullet points only highlighting the most important translational/clinical findings.
-
-**Language Requirement**: Write in natural, professional **Korean**, but keep key medical/scientific terminology in **English**.
-
-Title: {title}
-Abstract: {abstract}"""
-
-    safety_settings = {
-        HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
-        HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
-        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
-        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
-    }
-    
-    # 재시도 횟수를 4번으로 늘리고 대기 시간을 대폭 상향
+        return "- 제공된 Abstract가 없습니다."
+
+    messages = [
+        {
+            "role": "system",
+            "content": (
+                "You are a senior clinical researcher evaluating medical literature. "
+                "Return only plain text. No HTML. No markdown bold. "
+                "Exactly 2-3 bullet points, each line starts with '- '. "
+                "Write in professional Korean, preserving key medical terms in English."
+            ),
+        },
+        {
+            "role": "user",
+            "content": (
+                "Strictly summarize ONLY from the provided abstract.\n"
+                f"Title: {title}\n"
+                f"Abstract: {abstract}"
+            ),
+        },
+    ]
+
     for attempt in range(4):
         try:
-            response = model.generate_content(
-                prompt,
-                safety_settings=safety_settings,
-                generation_config=genai.GenerationConfig(
-                    max_output_tokens=1000,
-                    temperature=0.2
-                )
+            response = client.chat.completions.create(
+                model=MODEL_NAME,
+                messages=messages,
+                temperature=0.2,
+                max_tokens=450,
             )
-            
-            try:
-                raw_summary = response.text.strip()
-            except ValueError:
-                raw_summary = "안전 필터에 의해 요약이 차단되었거나 초록 내용이 불완전합니다."
-            
-            # --- [이메일 잘림 완벽 차단 로직] ---
-            # 1. 이메일 HTML을 깨뜨리는 부등호를 한글로 치환
-            safe_summary = raw_summary.replace("<", " 미만 ").replace(">", " 초과 ")
-            # 2. 모델이 몰래 넣었을지 모를 마크다운 볼드체 기호 강제 삭제
-            safe_summary = safe_summary.replace("**", "").replace("*", "")
-            # 3. 줄바꿈을 안전한 HTML 태그로 변환
-            safe_summary = safe_summary.replace('\n', '<br>')
-            # ------------------------------------
-            
-            print(f"Summary generated successfully. Waiting 20 seconds for rate limit (3 RPM)...")
-            time.sleep(20) # 1분당 최대 3회만 호출되도록 20초 딜레이 (절대 안전선)
+            raw_summary = response.choices[0].message.content.strip() if response.choices else ""
+            safe_summary = sanitize_summary(raw_summary)
+            print(f"Summary generated with OpenAI. Waiting {SUMMARY_DELAY_SECONDS}s for rate-limit safety...")
+            time.sleep(SUMMARY_DELAY_SECONDS)
             return safe_summary
-            
-        except Exception as e:
-            error_msg = str(e)
-            if "429" in error_msg or "Quota" in error_msg:
-                print(f"Rate limit hit! Waiting 70 seconds to reset quota... (Attempt {attempt+1}/4)")
-                time.sleep(70) # 1분 제한 초기화를 위해 완전히 70초 휴식 후 재시도
-            else:
-                return f"<p style='color:red;'>요약 생성 중 오류가 발생했습니다: {error_msg}</p>"
-                
-    return "<p style='color:red;'>서버 호출 횟수 제한으로 요약본을 가져오지 못했습니다.</p>"
+        except Exception as error:
+            error_msg = str(error)
+            if "429" in error_msg or "rate" in error_msg.lower() or "quota" in error_msg.lower():
+                wait_seconds = 20 + (attempt * 15)
+                print(f"OpenAI rate-limit/quota issue. Waiting {wait_seconds}s... (Attempt {attempt + 1}/4)")
+                time.sleep(wait_seconds)
+                continue
+            print(f"OpenAI summarize error: {error_msg}")
+            break
+
+    return simple_fallback_summary(abstract)
+
 
 def get_top_papers(all_papers, count=2):
-    high_impact_papers = [p for p in all_papers if p["is_high_impact"]]
-    other_papers = [p for p in all_papers if not p["is_high_impact"]]
-    sorted_papers = high_impact_papers + other_papers
-    return sorted_papers[:count]
+    high_impact = [paper for paper in all_papers if paper["is_high_impact"]]
+    others = [paper for paper in all_papers if not paper["is_high_impact"]]
+    return (high_impact + others)[:count]
+
+
+def build_summary_map(papers):
+    summary_map = {}
+    for paper in papers:
+        pmid = paper["pmid"]
+        if pmid not in summary_map:
+            summary_map[pmid] = openai_summarize(paper["abstract"], paper["title"])
+    return summary_map
+
 
-def format_paper_html(p, index=None):
-    summary = gemini_summarize(p["abstract"], p["title"])
+def format_paper_html(paper, summary_map, index=None):
+    summary_html = summary_to_html(summary_map.get(paper["pmid"], "- 요약 생성 실패"))
     index_str = f"{index}. " if index else ""
-    journal_str = f"<strong><span style='color:#b30000;'>{p['journal']} (High-Impact)</span></strong>" if p["is_high_impact"] else p['journal']
-    
+    journal = html.escape(paper["journal"])
+    journal_str = (
+        f"<strong><span style='color:#b30000;'>{journal} (High-Impact)</span></strong>"
+        if paper["is_high_impact"]
+        else journal
+    )
+
     return f"""
     <div style="margin-bottom: 25px; padding: 15px; border: 1px solid #ddd; border-radius: 5px; background-color: #fafafa;">
-        <h4 style="margin-top: 0; color: #2c3e50;">{index_str}{p['title']}</h4>
+        <h4 style="margin-top: 0; color: #2c3e50;">{index_str}{html.escape(paper['title'])}</h4>
         <p style="margin: 5px 0; font-size: 0.9em;">
             <strong>Journal:</strong> {journal_str}<br>
-            <strong>PMID:</strong> {p['pmid']} | <a href="{p['link']}" target="_blank" style="color: #2980b9;">PubMed 링크</a>
+            <strong>PMID:</strong> {paper['pmid']} | <a href="{paper['link']}" target="_blank" style="color: #2980b9;">PubMed 링크</a>
         </p>
         <div style="margin-top: 10px; padding-top: 10px; border-top: 1px dashed #ccc;">
             <strong style="color: #333;">💡 핵심 요약:</strong>
-            <div style="margin-top: 5px; color: #444; line-height: 1.8;">{summary}</div>
+            <div style="margin-top: 5px; color: #444; line-height: 1.6;">{summary_html}</div>
         </div>
     </div>
 """
 
-def send_email(neural_papers, sarc_papers):
-    if not neural_papers and not sarc_papers:
+
+def send_email(neural_papers, sarc_papers, ai_papers):
+    all_papers = neural_papers + sarc_papers + ai_papers
+    if not all_papers:
         print("No papers found for today's query.")
         return
-        
-    all_papers = neural_papers + sarc_papers
+
+    summary_map = build_summary_map(all_papers)
     top_papers = get_top_papers(all_papers, count=2)
-    
+
     body_html = f"""<html><body style="font-family: 'Malgun Gothic', dotum, sans-serif; line-height: 1.6; color: #333; max-width: 800px; margin: 0 auto;">
     <h2 style="color: #2c3e50; border-bottom: 2px solid #2c3e50; padding-bottom: 10px;">📊 {yesterday_date} PubMed 최신 동향 리포트</h2>
-    <p>연구자님, 지정하신 전문 분야의 최신 논문 검색 결과입니다. 학술적 근거와 명확한 사실에 기반하여 요약되었습니다.</p>
+    <p>신경재생/가소성, 사르코페니아, 의료 AI 연관 논문을 자동 수집·요약했습니다.</p>
 """
 
     if top_papers:
         body_html += """
-    <h3 style="color: #c0392b; margin-top: 30px; padding: 5px 10px; background-color: #fdebd0; border-left: 5px solid #c0392b;">
-        🌟 오늘의 주요 논문 Top 2
-    </h3>
-    <p style="font-size: 0.9em; color: #666;">* High-impact 저널 및 검색 적합도를 우선으로 선정되었습니다.</p>
+    <h3 style="color: #c0392b; margin-top: 30px; padding: 5px 10px; background-color: #fdebd0; border-left: 5px solid #c0392b;">🌟 오늘의 주요 논문 Top 2</h3>
+    <p style="font-size: 0.9em; color: #666;">High-impact 저널 우선 + 주제 적합도를 반영했습니다.</p>
 """
-        for p in top_papers:
-            body_html += format_paper_html(p)
+        for paper in top_papers:
+            body_html += format_paper_html(paper, summary_map)
 
     body_html += """
-    <h3 style="color: #2980b9; margin-top: 40px; padding: 5px 10px; background-color: #ebf5fb; border-left: 5px solid #2980b9;">
-        🧠 신경재생·가소성 섹션
-    </h3>
+    <h3 style="color: #2980b9; margin-top: 40px; padding: 5px 10px; background-color: #ebf5fb; border-left: 5px solid #2980b9;">🧠 신경재생·가소성 섹션</h3>
 """
     if neural_papers:
-        for i, p in enumerate(neural_papers, 1):
-            body_html += format_paper_html(p, index=i)
+        for idx, paper in enumerate(neural_papers, 1):
+            body_html += format_paper_html(paper, summary_map, index=idx)
     else:
         body_html += "<p>해당 분야의 새로운 논문이 없습니다.</p>"
 
     body_html += """
-    <h3 style="color: #27ae60; margin-top: 40px; padding: 5px 10px; background-color: #e9f7ef; border-left: 5px solid #27ae60;">
-        💪 사르코페니아 섹션
-    </h3>
+    <h3 style="color: #27ae60; margin-top: 40px; padding: 5px 10px; background-color: #e9f7ef; border-left: 5px solid #27ae60;">💪 사르코페니아 섹션</h3>
 """
     if sarc_papers:
-        for i, p in enumerate(sarc_papers, 1):
-            body_html += format_paper_html(p, index=i)
+        for idx, paper in enumerate(sarc_papers, 1):
+            body_html += format_paper_html(paper, summary_map, index=idx)
+    else:
+        body_html += "<p>해당 분야의 새로운 논문이 없습니다.</p>"
+
+    body_html += """
+    <h3 style="color: #7d3c98; margin-top: 40px; padding: 5px 10px; background-color: #f5eef8; border-left: 5px solid #7d3c98;">🤖 의료 AI·뇌 전기자극/진단 섹션</h3>
+"""
+    if ai_papers:
+        for idx, paper in enumerate(ai_papers, 1):
+            body_html += format_paper_html(paper, summary_map, index=idx)
     else:
         body_html += "<p>해당 분야의 새로운 논문이 없습니다.</p>"
 
     body_html += """
     <hr style="margin-top: 40px; border: 0; border-top: 1px solid #eee;">
-    <p style="text-align: center; color: #888; font-size: 0.85em;">
-        <em>본 이메일은 Gemini API를 활용하여 자동화된 검색 및 요약 결과를 제공합니다.</em>
-    </p>
+    <p style="text-align: center; color: #888; font-size: 0.85em;"><em>본 이메일은 PubMed + OpenAI API 기반 자동화 리포트입니다.</em></p>
     </body></html>
 """
-    
+
     msg = MIMEMultipart("alternative")
-    msg["Subject"] = f"[{yesterday_date}] 주요 논문 요약: 신경재생 & 사르코페니아 (총 {len(all_papers)}건)"
+    msg["Subject"] = f"[{yesterday_date}] 주요 논문 요약: Neuro + Sarcopenia + Medical AI (총 {len(all_papers)}건)"
     msg["From"] = f"Neuro-Sarc Alert <{GMAIL_USER}>"
     msg["To"] = TO_EMAIL
-    
     msg.attach(MIMEText(body_html, "html", "utf-8"))
-    
-    try:
-        with smtplib.SMTP("smtp.gmail.com", 587) as server:
-            server.starttls()
-            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
-            server.send_message(msg)
-        print("✅ HTML Email sent successfully!")
-    except Exception as e:
-        print(f"이메일 전송 중 오류 발생: {e}")
 
-if __name__ == "__main__":
-    print("=== Neuro-Sarc Daily Alert Script Started ===")
+    with smtplib.SMTP("smtp.gmail.com", 587) as server:
+        server.starttls()
+        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
+        server.send_message(msg)
+    print("✅ HTML Email sent successfully!")
+
+
+def main():
+    print("=== Neuro-Sarc Alert Script Started ===")
     print(f"Searching papers for date: {yesterday_date}")
-    
-    # 무료 API 속도(20초 딜레이)를 고려하여 추출 논문 수를 총 10건(5건씩)으로 조절하여 전체 구동 시간을 약 3~4분 내로 안정화
-    neural = fetch_papers(NEURAL_QUERY, max_results=5) 
-    sarc = fetch_papers(SARC_QUERY, max_results=5)
-    
-    send_email(neural, sarc)
+
+    if not should_run_today():
+        print("Today is not Monday/Wednesday/Saturday. Skipping run.")
+        return
+
+    neural = fetch_papers(NEURAL_QUERY, topic="neural", max_results=MAX_RESULTS_NEURAL)
+    sarc = fetch_papers(SARC_QUERY, topic="sarcopenia", max_results=MAX_RESULTS_SARC)
+    ai = fetch_papers(AI_QUERY, topic="medical-ai", max_results=MAX_RESULTS_AI)
+
+    send_email(neural, sarc, ai)
     print("=== Script completed SUCCESSFULLY ===")
+
+
+if __name__ == "__main__":
+    main()

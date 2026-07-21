import os
import re
import json
import httpx

RAPIDAPI_KEY = os.environ["RAPIDAPI_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SENDGRID_API_KEY = os.environ["SENDGRID_API_KEY"]
FROM_EMAIL = os.environ["FROM_EMAIL"]
TO_EMAIL = os.environ["TO_EMAIL"]

TARGET_COMPANIES = ["Toro","Polaris","Graco","Donaldson","Medtronic",
                     "Boston Scientific","3M","Stratasys","Proto Labs","TSI"]

QUERIES = [
    "mechanical engineer Minnesota",
    "product development engineer Minnesota",
    "quality engineer Minnesota",
    "manufacturing engineer Minnesota",
    "design engineer Minnesota",
    "process engineer Minnesota",
    "application engineer Minnesota"
]

def fetch_jobs():
    all_jobs = []
    seen_ids = set()
    with httpx.Client(timeout=60.0) as client:
        for q in QUERIES:
            try:
                resp = client.get(
                    "https://jsearch.p.rapidapi.com/search",
                    headers={
                        "X-RapidAPI-Key": RAPIDAPI_KEY,
                        "X-RapidAPI-Host": "jsearch.p.rapidapi.com"
                    },
                    params={
                        "query": q,
                        "location": "Minneapolis, Minnesota, United States",
                        "distance": "30",
                        "page": "1",
                        "num_results": "10",
                        "employment_types": "FULLTIME"
                    }
                )
                data = resp.json()
                for job in data.get("data", []):
                    job_id = job.get("job_id", "")
                    if job_id in seen_ids:
                        continue
                    seen_ids.add(job_id)
                    salary_min = job.get("job_min_salary")
                    salary_max = job.get("job_max_salary")
                    salary_str = f"${int(salary_min):,} - ${int(salary_max):,}" if salary_min and salary_max else "Not listed"
                    posted = job.get("job_posted_at_datetime_utc", "")[:10] if job.get("job_posted_at_datetime_utc") else ""
                    highlights = job.get("job_highlights", {})
                    company = job.get("employer_name", "")
                    all_jobs.append({
                        "title": job.get("job_title", ""),
                        "company": company,
                        "location": f"{job.get('job_city','Minneapolis')}, {job.get('job_state','MN')}",
                        "salary": salary_str,
                        "posted": posted,
                        "apply_url": job.get("job_apply_link", "https://www.linkedin.com/jobs"),
                        "description": job.get("job_description", "")[:300],
                        "responsibilities": highlights.get("Responsibilities", [])[:3],
                        "is_target_company": any(tc.lower() in company.lower() for tc in TARGET_COMPANIES)
                    })
            except Exception as e:
                print(f"Error fetching '{q}': {e}")
    return all_jobs

def score_jobs(jobs):
    jobs_summary = "\n".join(
        f"{i+1}. \"{j['title']}\" at {j['company']} in {j['location']}. Salary: {j['salary']}. "
        f"Responsibilities: {'; '.join(j['responsibilities']) or j['description'][:150]}"
        for i, j in enumerate(jobs)
    )

    system_prompt = """You are a job search assistant for Peter, a mechanical engineering student graduating December 2025, interning at The Toro Company in Bloomington MN with DFMEA/PDRA/QMS experience. He lives in Minnetrista MN, 30-mile commute limit.
Distance refs from Minnetrista: Polaris Medina 8mi, Proto Labs Maple Plain 10mi, Eden Prairie 15mi, Toro Bloomington 18mi, Donaldson Bloomington 20mi, Graco Minneapolis 22mi, Medtronic Fridley 25mi, 3M Maplewood 30mi.
Respond ONLY with a valid JSON array, no markdown, no explanation."""

    user_msg = f"""Score and rank these {len(jobs)} jobs for Peter. Return a JSON array of {len(jobs)} objects in the same order.

{jobs_summary}

Each object: {{"score": number 0-100, "commuteMi": "estimate", "whyFit": "1 sentence why this fits Peter"}}
Scoring: title matches (Mechanical/Design/Quality/Manufacturing/Product Development/Application/Process Engineer) +30, target company (Toro/Polaris/Graco/Donaldson/Medtronic/Boston Scientific/3M/Stratasys/Proto Labs/TSI) +20, commute under 30mi +20, relevant to DFMEA/QMS/product dev +20, strong entry salary +10.
Return ONLY the JSON array."""

    with httpx.Client(timeout=120.0) as client:
        resp = client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 4000,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_msg}]
            }
        )
        data = resp.json()
        if data.get("error"):
            print("Claude error:", data["error"])
            return jobs
        raw = data["content"][0]["text"]
        match = re.search(r'\[[\s\S]*\]', raw)
        if not match:
            return jobs
        try:
            scores = json.loads(match.group())
            for i, j in enumerate(jobs):
                if i < len(scores):
                    j["score"] = scores[i].get("score", 50)
                    j["commuteMi"] = scores[i].get("commuteMi", "?")
                    j["whyFit"] = scores[i].get("whyFit", "")
        except Exception as e:
            print("Parse error:", e)
        return jobs

def build_email_html(top_jobs, new_count):
    rows = ""
    for j in top_jobs:
        target_badge = ' <span style="background:#EEEDFE;color:#3C3489;font-size:11px;padding:2px 8px;border-radius:10px;margin-left:6px">Target co.</span>' if j.get("is_target_company") else ""
        rows += f"""
        <div style="border:1px solid #dddbd0;border-radius:12px;padding:16px;margin-bottom:12px">
          <div style="font-size:16px;font-weight:600;color:#1a1a18">{j['title']}{target_badge}</div>
          <div style="font-size:13px;color:#888780;margin-bottom:8px">{j['company']} &middot; {j['location']}</div>
          <div style="font-size:13px;color:#1a1a18;margin-bottom:6px">
            Salary: {j['salary']} &nbsp;|&nbsp; Commute: ~{j.get('commuteMi','?')} mi &nbsp;|&nbsp; Score: {j.get('score','?')}/100
          </div>
          <div style="font-size:13px;color:#5f5e5a;font-style:italic;margin-bottom:10px">{j.get('whyFit','')}</div>
          <a href="{j['apply_url']}" style="background:#1a1a18;color:#fff;padding:8px 16px;border-radius:8px;text-decoration:none;font-size:13px">Apply now</a>
        </div>"""

    return f"""
    <div style="font-family:-apple-system,Segoe UI,sans-serif;max-width:600px;margin:0 auto">
      <h2 style="color:#1a1a18">Your weekly job digest</h2>
      <p style="color:#888780;font-size:14px">{new_count} new listings found this week. Here are your top matches:</p>
      {rows}
      <p style="color:#b4b2a9;font-size:12px;margin-top:20px">Sent automatically by your job search agent.</p>
    </div>"""

def send_email(html_content, job_count):
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {SENDGRID_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "personalizations": [{"to": [{"email": TO_EMAIL}]}],
                "from": {"email": FROM_EMAIL, "name": "Job Search Agent"},
                "subject": f"Your weekly job digest — {job_count} top matches",
                "content": [{"type": "text/html", "value": html_content}]
            }
        )
        print("SendGrid status:", resp.status_code)
        if resp.status_code >= 300:
            print("SendGrid response:", resp.text)

def main():
    print("Fetching jobs...")
    jobs = fetch_jobs()
    print(f"Found {len(jobs)} jobs")

    if not jobs:
        print("No jobs found, skipping email")
        return

    print("Scoring jobs with Claude...")
    jobs = score_jobs(jobs)

    top_jobs = sorted(jobs, key=lambda j: j.get("score", 0), reverse=True)[:10]

    print("Sending email...")
    html = build_email_html(top_jobs, len(jobs))
    send_email(html, len(top_jobs))
    print("Done!")

if __name__ == "__main__":
    main()

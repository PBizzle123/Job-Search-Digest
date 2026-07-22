import os
import re
import json
import httpx
from datetime import datetime, timezone

RAPIDAPI_KEY = os.environ["RAPIDAPI_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SENDGRID_API_KEY = os.environ["SENDGRID_API_KEY"]
FROM_EMAIL = os.environ["FROM_EMAIL"]
TO_EMAIL = os.environ["TO_EMAIL"]
PAGES_URL = os.environ.get("PAGES_URL", "")

SEEN_JOBS_PATH = "data/seen_jobs.json"

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

def load_seen_jobs():
    if not os.path.exists(SEEN_JOBS_PATH):
        return {}
    try:
        with open(SEEN_JOBS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Could not read seen jobs file: {e}")
        return {}

def save_seen_jobs(seen):
    os.makedirs("data", exist_ok=True)
    with open(SEEN_JOBS_PATH, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2)

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
                    if not job_id or job_id in seen_ids:
                        continue
                    seen_ids.add(job_id)
                    salary_min = job.get("job_min_salary")
                    salary_max = job.get("job_max_salary")
                    salary_str = f"${int(salary_min):,} - ${int(salary_max):,}" if salary_min and salary_max else "Not listed"
                    posted = job.get("job_posted_at_datetime_utc", "")[:10] if job.get("job_posted_at_datetime_utc") else ""
                    highlights = job.get("job_highlights", {})
                    company = job.get("employer_name", "")
                    all_jobs.append({
                        "job_id": job_id,
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

def mark_new_jobs(jobs, seen):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    new_count = 0
    for j in jobs:
        if j["job_id"] not in seen:
            j["is_new"] = True
            seen[j["job_id"]] = today
            new_count += 1
        else:
            j["is_new"] = False
    return new_count

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

def badge_color(score):
    if score >= 80:
        return "#EAF3DE", "#27500A"
    elif score >= 60:
        return "#FAEEDA", "#633806"
    else:
        return "#FCEBEB", "#791F1F"

def new_badge_html():
    return '<span style="background:#EAF3DE;color:#27500A;font-size:11px;padding:2px 9px;border-radius:12px;margin-left:8px">New</span>'

def target_badge_html():
    return '<span style="background:#EEEDFE;color:#3C3489;font-size:11px;padding:2px 9px;border-radius:12px;margin-left:8px">Target company</span>'

def build_email_html(top_jobs, total_count, new_jobs_count, pages_url):
    rows = ""
    for j in top_jobs:
        badges = ""
        if j.get("is_target_company"):
            badges += target_badge_html()
        if j.get("is_new"):
            badges += new_badge_html()
        rows += f"""
        <div style="border:1px solid #dddbd0;border-radius:12px;padding:16px;margin-bottom:12px">
          <div style="font-size:16px;font-weight:600;color:#1a1a18">{j['title']}{badges}</div>
          <div style="font-size:13px;color:#888780;margin-bottom:8px">{j['company']} &middot; {j['location']}</div>
          <div style="font-size:13px;color:#1a1a18;margin-bottom:6px">
            Salary: {j['salary']} &nbsp;|&nbsp; Commute: ~{j.get('commuteMi','?')} mi &nbsp;|&nbsp; Score: {j.get('score','?')}/100
          </div>
          <div style="font-size:13px;color:#5f5e5a;font-style:italic;margin-bottom:10px">{j.get('whyFit','')}</div>
          <a href="{j['apply_url']}" style="background:#1a1a18;color:#fff;padding:8px 16px;border-radius:8px;text-decoration:none;font-size:13px">Apply now</a>
        </div>"""

    view_all_link = ""
    if pages_url:
        view_all_link = f"""
        <div style="background:#f5f5f0;border-radius:12px;padding:14px 16px;margin-bottom:20px;text-align:center">
          <a href="{pages_url}" style="color:#1a1a18;font-size:14px;font-weight:600;text-decoration:none">
            View all {total_count} jobs found this week &rarr;
          </a>
        </div>"""

    return f"""
    <div style="font-family:-apple-system,Segoe UI,sans-serif;max-width:600px;margin:0 auto">
      <h2 style="color:#1a1a18">Your weekly job digest</h2>
      <p style="color:#888780;font-size:14px">{new_jobs_count} new listing{"s" if new_jobs_count != 1 else ""} since last check &middot; {total_count} total open positions found. Here are your top {len(top_jobs)} matches:</p>
      {view_all_link}
      {rows}
      <p style="color:#b4b2a9;font-size:12px;margin-top:20px">Sent automatically by your job search agent.</p>
    </div>"""

def build_full_page_html(all_jobs):
    sorted_jobs = sorted(all_jobs, key=lambda j: j.get("score", 0), reverse=True)
    now = datetime.now(timezone.utc).strftime("%B %d, %Y at %I:%M %p UTC")
    new_count = sum(1 for j in sorted_jobs if j.get("is_new"))

    cards = ""
    for j in sorted_jobs:
        bg, fg = badge_color(j.get("score", 0))
        badges = ""
        if j.get("is_target_company"):
            badges += target_badge_html()
        if j.get("is_new"):
            badges += new_badge_html()
        cards += f"""
        <div style="background:#fff;border:1px solid #dddbd0;border-radius:12px;padding:18px 20px;margin-bottom:12px">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap">
            <div>
              <div style="font-size:16px;font-weight:600;color:#1a1a18">{j['title']}{badges}</div>
              <div style="font-size:13px;color:#888780;margin:2px 0 8px">{j['company']} &middot; {j['location']}</div>
              <div style="font-size:13px;color:#1a1a18">
                {j['salary']} &nbsp;|&nbsp; ~{j.get('commuteMi','?')} mi from Minnetrista &nbsp;|&nbsp; Posted {j.get('posted') or 'recently'}
              </div>
            </div>
            <span style="background:{bg};color:{fg};font-size:13px;font-weight:600;padding:4px 12px;border-radius:14px;white-space:nowrap">
              {j.get('score','?')}/100
            </span>
          </div>
          <div style="font-size:13px;color:#5f5e5a;font-style:italic;margin:10px 0">{j.get('whyFit','')}</div>
          <a href="{j['apply_url']}" style="color:#1a1a18;font-size:13px;font-weight:600;text-decoration:underline">Apply now &rarr;</a>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Weekly job listings</title>
</head>
<body style="font-family:-apple-system,Segoe UI,sans-serif;background:#f5f5f0;margin:0;padding:2rem 1rem">
  <div style="max-width:700px;margin:0 auto">
    <h1 style="color:#1a1a18;font-size:22px">All jobs found this week</h1>
    <p style="color:#888780;font-size:13px;margin-bottom:24px">{len(sorted_jobs)} listings ({new_count} new) &middot; Last updated {now}</p>
    {cards}
  </div>
</body>
</html>"""

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

    print("Checking for new vs. previously seen jobs...")
    seen = load_seen_jobs()
    new_jobs_count = mark_new_jobs(jobs, seen)
    save_seen_jobs(seen)
    print(f"{new_jobs_count} new jobs since last run")

    print("Scoring jobs with Claude...")
    jobs = score_jobs(jobs)

    top_jobs = sorted(jobs, key=lambda j: j.get("score", 0), reverse=True)[:10]

    print("Building full listings page...")
    os.makedirs("docs", exist_ok=True)
    full_page = build_full_page_html(jobs)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(full_page)

    print("Sending email...")
    email_html = build_email_html(top_jobs, len(jobs), new_jobs_count, PAGES_URL)
    send_email(email_html, len(top_jobs))
    print("Done!")

if __name__ == "__main__":
    main()
      

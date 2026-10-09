import os
import uuid
import csv
import datetime
from datetime import timedelta, timezone
from urllib.parse import urlparse
import psycopg2
from unittest.mock import patch

# pyrefly: ignore [missing-import]
from fastapi import FastAPI, HTTPException 
# pyrefly: ignore [missing-import]
from fastapi.responses import HTMLResponse, JSONResponse
# pyrefly: ignore [missing-import]
from fastapi.staticfiles import StaticFiles 
# pyrefly: ignore [missing-import]
from pydantic import BaseModel
import uvicorn

def load_csv_data(filepath):
    if not os.path.exists(filepath):
        return []
    with open(filepath, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        return list(reader)

def setup_presentation_data(conn, cur):
    csv_candidates = load_csv_data('data/candidates.csv')
    csv_voters = load_csv_data('data/voters.csv')
    
    if not csv_candidates or not csv_voters:
        raise ValueError("Missing data/candidates.csv or data/voters.csv")

    base_session = "PRESENTATION-DEMO-SESS"
    session_id = base_session
    counter = 1
    
    voter1_id = None
    voter2_id = None
    cands = []
    is_new = False
    
    now = datetime.datetime.now(timezone.utc)
    
    while True:
        cur.execute("SELECT title, start_time, end_time, status FROM voting_sessions WHERE session_id = %s", (session_id,))
        row = cur.fetchone()
        
        if row:
            title, st, et, status = row
            if title != 'Presentation Demo Session':
                raise ValueError(f"Session {session_id} exists with conflicting title.")
            
            if status != 'ACTIVE' or not (st <= now <= et):
                session_id = f"{base_session}-{counter}"
                counter += 1
                continue
                
            cur.execute("""
                SELECT sv.voter_id 
                FROM session_voters sv
                WHERE sv.session_id = %s
                AND NOT EXISTS (SELECT 1 FROM voter_participation vp WHERE vp.session_id = %s AND vp.voter_id = sv.voter_id)
            """, (session_id, session_id))
            unvoted = [r[0] for r in cur.fetchall()]
            
            if len(unvoted) >= 2:
                voter1_id = unvoted[0]
                voter2_id = unvoted[1]
                
                v1_csv = next((r for r in csv_voters if r['voter_id'] == voter1_id), None)
                v2_csv = next((r for r in csv_voters if r['voter_id'] == voter2_id), None)
                if not v1_csv or not v2_csv:
                    session_id = f"{base_session}-{counter}"
                    counter += 1
                    continue
                
                cur.execute("""
                    SELECT c.candidate_id, c.candidate_name 
                    FROM candidates c
                    JOIN session_choices sc ON c.candidate_id = sc.candidate_id
                    WHERE sc.session_id = %s
                """, (session_id,))
                cands = cur.fetchall()
                if not cands:
                    raise ValueError(f"Reusable session {session_id} has no candidates.")
                
                cands_in_csv = True
                for cid, cname in cands:
                    if not any(r['candidate_id'] == cid for r in csv_candidates):
                        cands_in_csv = False
                        break
                        
                if not cands_in_csv:
                    session_id = f"{base_session}-{counter}"
                    counter += 1
                    continue
                
                break
            else:
                session_id = f"{base_session}-{counter}"
                counter += 1
                continue
        else:
            print(f"[Setup] Creating new presentation session fixture {session_id}...")
            start_time = now - timedelta(hours=1)
            end_time = now + timedelta(hours=1)
            
            cur.execute("""
                INSERT INTO voting_sessions (session_id, title, session_type, start_time, end_time, status)
                VALUES (%s, 'Presentation Demo Session', 'candidate_election', %s, %s, 'DRAFT')
            """, (session_id, start_time, end_time))
            
            cands = [(r['candidate_id'], r['candidate_name']) for r in csv_candidates[:3]]
            for c_id, c_name in cands:
                cur.execute("SELECT 1 FROM candidates WHERE candidate_id = %s", (c_id,))
                if not cur.fetchone():
                    cur.execute("INSERT INTO candidates (candidate_id, candidate_name) VALUES (%s, %s)", (c_id, c_name))
                
                cur.execute("""
                    INSERT INTO session_choices (session_choice_id, session_id, session_type, candidate_id, option_id)
                    VALUES (%s, %s, 'candidate_election', %s, NULL)
                """, (str(uuid.uuid4()), session_id, c_id))
                
            v_list = csv_voters[:2]
            voter1_id = v_list[0]['voter_id']
            voter2_id = v_list[1]['voter_id']
            
            for v_dict in v_list:
                v_id = v_dict['voter_id']
                cur.execute("SELECT name, department, role FROM voters WHERE voter_id = %s", (v_id,))
                db_voter = cur.fetchone()
                if db_voter:
                    if db_voter != (v_dict['name'], v_dict['department'], v_dict['role']):
                        raise ValueError(f"Voter {v_id} exists in DB but with conflicting data: {db_voter}")
                else:
                    cur.execute("INSERT INTO voters (voter_id, name, department, role) VALUES (%s, %s, %s, %s)", 
                                (v_id, v_dict['name'], v_dict['department'], v_dict['role']))
                                
                cur.execute("INSERT INTO session_voters (session_id, voter_id) VALUES (%s, %s)", (session_id, v_id))
                
            is_new = True
            break
            
    issuer = "presentation-demo-issuer"
    sub1 = f"presentation-subject-{voter1_id}"
    sub2 = f"presentation-subject-{voter2_id}"

    for v_id, sub in [(voter1_id, sub1), (voter2_id, sub2)]:
        cur.execute("SELECT issuer, subject FROM voter_identities WHERE voter_id = %s", (v_id,))
        db_id = cur.fetchone()
        if db_id:
            if db_id != (issuer, sub):
                raise ValueError(f"Voter {v_id} already has a conflicting identity mapping: {db_id}")
        else:
            cur.execute("INSERT INTO voter_identities (issuer, subject, voter_id) VALUES (%s, %s, %s)", (issuer, sub, v_id))

    conn.commit()
    return session_id, cands, voter1_id, voter2_id, issuer, sub1, sub2, is_new

demo_app = FastAPI(title="IPD Voting Demo UI")

# Global variables for demo context
demo_context = {}

@demo_app.get("/api/config")
def get_config():
    db_url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        raise HTTPException(status_code=500, detail="UNAVAILABLE: No database connection URL provided.")

    try:
        parsed = urlparse(db_url)
        db_name = parsed.path.lstrip('/')
    except Exception:
        raise HTTPException(status_code=500, detail="UNAVAILABLE: Failed to parse connection URL.")

    if db_name != "test_ipd_voting":
        raise HTTPException(status_code=500, detail=f"UNAVAILABLE: Must use 'test_ipd_voting' exactly. Got '{db_name}'.")
    
    os.environ["DATABASE_URL"] = db_url

    import api
    repo = api.engine.repo

    try:
        conn = psycopg2.connect(db_url)
        with conn.cursor() as cur:
            session_id, cands, voter1_id, voter2_id, issuer, sub1, sub2, is_new = setup_presentation_data(conn, cur)
            
            if is_new:
                repo.update_session_status(session_id, 'APPROVED')
                repo.update_session_status(session_id, 'ACTIVE')
    except Exception as e:
        if 'conn' in locals():
            conn.close()
        raise HTTPException(status_code=500, detail=f"Failed setting up presentation data: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

    demo_context.update({
        'session_id': session_id,
        'voter1_id': voter1_id,
        'voter2_id': voter2_id,
        'issuer': issuer,
        'sub1': sub1,
        'sub2': sub2,
    })

    return {
        "db_name": db_name,
        "session_id": session_id,
        "candidates": [{"id": c[0], "name": c[1]} for c in cands],
        "voter_normal": voter1_id,
        "voter_attack": voter2_id
    }

class RunRequest(BaseModel):
    candidate_id: str
    scenario: str # 'normal' or 'attack'

@demo_app.post("/api/run")
def run_scenario(req: RunRequest):
    if not demo_context:
        raise HTTPException(status_code=400, detail="Config not loaded.")

    session_id = demo_context['session_id']
    if req.scenario == 'attack':
        voter_id = demo_context['voter2_id']
        voter_sub = demo_context['sub2']
        eavesdrop = True
        bb84_seed = 123
    else:
        voter_id = demo_context['voter1_id']
        voter_sub = demo_context['sub1']
        eavesdrop = False
        bb84_seed = 1001

    voter_issuer = demo_context['issuer']

    # pyrefly: ignore [missing-import]
    from fastapi.testclient import TestClient
    import api
    from api import app, get_current_principal, Principal
    from voting.ballot_encoding import encode_choice
    from vol2_bb84 import run_secure_bb84
    from vol3_pqc import encrypt_vote
    
    repo = api.engine.repo
    original_encode_choice = encode_choice
    original_run_secure_bb84 = run_secure_bb84
    original_encrypt_vote = encrypt_vote
    original_reserve = repo.reserve_vote
    original_finalize = repo.finalize_vote

    def mock_get_principal():
        return Principal(issuer=voter_issuer, subject=voter_sub, permissions=[])

    app.dependency_overrides[get_current_principal] = mock_get_principal
    client = TestClient(app)

    steps = []
    encryption_ran = False
    reached_eavesdrop = False
    qber_val = None
    qber_thresh = None

    steps.append({"stage": "M1", "status": "success", "message": f"Session/Choice selected: Session {session_id}, Candidate {req.candidate_id}"})
    
    def mock_reserve(*args, **kwargs):
        res = original_reserve(*args, **kwargs)
        if res[0]:
            steps.append({"stage": "M2", "status": "success", "message": f"Voter eligibility passed (synthetic principal mapped; OIDC bypassed)."})
        return res

    def mock_encode_choice(*args, **kwargs):
        res = original_encode_choice(*args, **kwargs)
        steps.append({"stage": "M3", "status": "success", "message": "M3 encoding completed."})
        return res

    def mock_run_secure_bb84(*args, **kwargs):
        nonlocal reached_eavesdrop, qber_val, qber_thresh
        reached_eavesdrop = True
        kwargs['seed'] = bb84_seed
        if eavesdrop:
            kwargs['eavesdrop'] = True
        res = original_run_secure_bb84(*args, **kwargs)
        qber_val = res.get('qber', 0.0)
        qber_thresh = res.get('qber_threshold')
        thresh_str = f"{qber_thresh:.2%}" if qber_thresh is not None else "unavailable"
        msg = f"Simulated BB84 key generation completed. QBER {qber_val:.2%} vs threshold {thresh_str}."
        if eavesdrop and qber_thresh is not None and qber_val > qber_thresh:
            steps.append({"stage": "M4", "status": "abort", "message": f"{msg} Eavesdropping detected!"})
        else:
            steps.append({"stage": "M4", "status": "success", "message": msg})
        return res

    def mock_encrypt_vote(*args, **kwargs):
        res = original_encrypt_vote(*args, **kwargs)
        nonlocal encryption_ran
        encryption_ran = True
        steps.append({"stage": "Encrypt", "status": "success", "message": "Fernet/AES PQC Encryption ran."})
        return res
        
    def mock_finalize(*args, **kwargs):
        return original_finalize(*args, **kwargs)
        
    db_url = os.environ.get("DATABASE_URL")
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM ballots WHERE session_id = %s", (session_id,))
    initial_ballots = cur.fetchone()[0]

    ik = str(uuid.uuid4())
    
    with patch.object(repo, 'reserve_vote', side_effect=mock_reserve), \
         patch.object(repo, 'finalize_vote', side_effect=mock_finalize), \
         patch('voting.voting_engine.encode_choice', side_effect=mock_encode_choice), \
         patch('voting.voting_engine.run_secure_bb84', side_effect=mock_run_secure_bb84), \
         patch('voting.voting_engine.encrypt_vote', side_effect=mock_encrypt_vote):
        
        response = client.post("/vote", json={"session_id": session_id, "candidate_id": req.candidate_id}, headers={"Idempotency-Key": ik})
        
    cur.execute("SELECT COUNT(*) FROM ballots WHERE session_id = %s", (session_id,))
    final_ballots = cur.fetchone()[0]
    
    cur.execute("SELECT status FROM voter_participation WHERE session_id = %s AND voter_id = %s", (session_id, voter_id))
    part_row = cur.fetchone()
    part_status = part_row[0] if part_row else None
    
    cur.close()
    conn.close()

    ballots_written = final_ballots - initial_ballots

    if eavesdrop:
        qber_exceeded = (qber_val is not None and qber_thresh is not None and qber_val > qber_thresh)
        is_422 = (response.status_code == 422)
        has_qber_reason = 'qber' in str(response.json().get('detail')).lower()
        
        if reached_eavesdrop and is_422 and has_qber_reason and qber_exceeded and not encryption_ran and ballots_written == 0:
            steps.append({"stage": "Storage", "status": "success", "message": "Verification: Attack prevented exactly as expected (HTTP 422, QBER exceeded, encryption skipped, 0 ballots)."})
        else:
            steps.append({"stage": "Storage", "status": "error", "message": f"Verification Failed! HTTP={response.status_code}, QBER>{qber_thresh}={qber_exceeded}, EncryptRan={encryption_ran}, Ballots={ballots_written}"})
    else:
        if part_status == 'COMMITTED' and ballots_written == 1 and encryption_ran and response.status_code == 200:
            steps.append({"stage": "Storage", "status": "success", "message": "Verification: Success. HTTP 200 OK. Participation COMMITTED, exactly 1 ballot written."})
        else:
            steps.append({"stage": "Storage", "status": "error", "message": f"Verification Failed! HTTP={response.status_code}, COMMITTED={part_status}, EncryptRan={encryption_ran}, Ballots={ballots_written}"})

    return {"steps": steps}

@demo_app.get("/")
def read_index():
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>IPD Voting - Interactive Presentation Demo</title>
        <style>
            :root {
                --bg: #0f172a;
                --surface: #1e293b;
                --primary: #3b82f6;
                --primary-hover: #2563eb;
                --danger: #ef4444;
                --danger-hover: #dc2626;
                --text: #f8fafc;
                --text-muted: #94a3b8;
                --success: #10b981;
                --border: #334155;
            }
            body {
                font-family: 'Inter', system-ui, sans-serif;
                background-color: var(--bg);
                color: var(--text);
                margin: 0;
                padding: 2rem;
                display: flex;
                flex-direction: column;
                align-items: center;
                min-height: 100vh;
            }
            .container {
                width: 100%;
                max-width: 800px;
                background: var(--surface);
                border-radius: 12px;
                box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.5);
                padding: 2rem;
                box-sizing: border-box;
            }
            h1 {
                margin-top: 0;
                font-size: 1.8rem;
                text-align: center;
                background: -webkit-linear-gradient(45deg, #3b82f6, #8b5cf6);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
            }
            .info-box {
                background: rgba(59, 130, 246, 0.1);
                border: 1px solid var(--primary);
                border-radius: 8px;
                padding: 1rem;
                margin-bottom: 2rem;
                font-size: 0.9rem;
                color: var(--text-muted);
            }
            .info-box strong { color: var(--text); }
            select, button {
                width: 100%;
                padding: 12px;
                margin-bottom: 1rem;
                border-radius: 8px;
                border: 1px solid var(--border);
                background: var(--bg);
                color: var(--text);
                font-size: 1rem;
                outline: none;
                transition: all 0.2s;
            }
            select:focus { border-color: var(--primary); }
            .btn-group {
                display: flex;
                gap: 1rem;
                margin-top: 1rem;
            }
            button {
                cursor: pointer;
                border: none;
                font-weight: 600;
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 8px;
            }
            .btn-normal {
                background: var(--primary);
                color: white;
            }
            .btn-normal:hover { background: var(--primary-hover); }
            .btn-attack {
                background: var(--danger);
                color: white;
            }
            .btn-attack:hover { background: var(--danger-hover); }
            button:disabled { opacity: 0.5; cursor: not-allowed; }
            
            .timeline {
                margin-top: 2rem;
                border-left: 2px solid var(--border);
                padding-left: 1.5rem;
                display: none;
            }
            .timeline-step {
                position: relative;
                margin-bottom: 1.5rem;
                opacity: 0;
                transform: translateY(10px);
                animation: slideIn 0.5s forwards;
            }
            @keyframes slideIn {
                to { opacity: 1; transform: translateY(0); }
            }
            .timeline-step::before {
                content: '';
                position: absolute;
                left: -1.8rem;
                top: 0.3rem;
                width: 12px;
                height: 12px;
                border-radius: 50%;
                background: var(--border);
            }
            .timeline-step.success::before { background: var(--success); box-shadow: 0 0 10px var(--success); }
            .timeline-step.abort::before { background: var(--danger); box-shadow: 0 0 10px var(--danger); }
            .timeline-step.error::before { background: #f59e0b; }
            
            .step-title { font-weight: 600; margin-bottom: 0.25rem; }
            .step-desc { font-size: 0.9rem; color: var(--text-muted); }
            
            #loader { text-align: center; color: var(--primary); display: none; margin-top: 1rem; font-weight: 600; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>IPD Quantum Voting Presentation</h1>
            <div class="info-box" id="config-info">Loading verified configuration...</div>
            
            <label for="candidate-select"><strong>Select Candidate for Ballot:</strong></label>
            <select id="candidate-select"></select>
            
            <div class="btn-group">
                <button class="btn-normal" id="btn-normal" onclick="runScenario('normal')">
                    <svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>
                    Run Normal Vote
                </button>
                <button class="btn-attack" id="btn-attack" onclick="runScenario('attack')">
                    <svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path></svg>
                    Run Eavesdropping Attack
                </button>
            </div>
            
            <div id="loader">Running simulation...</div>
            <div class="timeline" id="timeline"></div>
        </div>

        <script>
            let currentConfig = null;

            async function init() {
                try {
                    const res = await fetch('/api/config');
                    if(!res.ok) throw new Error(await res.text());
                    const config = await res.json();
                    currentConfig = config;
                    
                    document.getElementById('config-info').innerHTML = `
                        Database Target: <strong>${config.db_name}</strong><br>
                        Presentation Session: <strong>${config.session_id}</strong><br>
                        Normal Voter Loaded (M1): <strong>Eligible</strong><br>
                        Attack Voter Loaded (M1): <strong>Eligible</strong><br>
                        <em>Note: OIDC login is bypassed via synthetic principal injection for this runner.</em><br>
                        <em>Note: BB84 M4 channel is simulated locally.</em>
                    `;
                    
                    const select = document.getElementById('candidate-select');
                    config.candidates.forEach(c => {
                        const opt = document.createElement('option');
                        opt.value = c.id;
                        opt.textContent = `${c.name} (${c.id})`;
                        select.appendChild(opt);
                    });
                } catch(e) {
                    document.getElementById('config-info').innerHTML = `<span style="color:var(--danger)">Setup Failed: ${e.message}</span>`;
                    document.getElementById('btn-normal').disabled = true;
                    document.getElementById('btn-attack').disabled = true;
                }
            }

            async function runScenario(scenario) {
                const btnN = document.getElementById('btn-normal');
                const btnA = document.getElementById('btn-attack');
                const select = document.getElementById('candidate-select');
                const timeline = document.getElementById('timeline');
                const loader = document.getElementById('loader');
                
                btnN.disabled = true;
                btnA.disabled = true;
                select.disabled = true;
                timeline.style.display = 'none';
                timeline.innerHTML = '';
                loader.style.display = 'block';
                
                try {
                    const res = await fetch('/api/run', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            candidate_id: select.value,
                            scenario: scenario
                        })
                    });
                    
                    if(!res.ok) throw new Error(await res.text());
                    
                    const data = await res.json();
                    
                    loader.style.display = 'none';
                    timeline.style.display = 'block';
                    
                    // Render steps with artificial delay for presentation effect
                    for (let i = 0; i < data.steps.length; i++) {
                        const step = data.steps[i];
                        await new Promise(r => setTimeout(r, 600));
                        
                        const div = document.createElement('div');
                        div.className = `timeline-step ${step.status}`;
                        div.innerHTML = `
                            <div class="step-title">${step.stage}</div>
                            <div class="step-desc">${step.message}</div>
                        `;
                        timeline.appendChild(div);
                    }
                } catch(e) {
                    loader.style.display = 'none';
                    timeline.style.display = 'block';
                    timeline.innerHTML = `<div class="timeline-step error"><div class="step-title">System Error</div><div class="step-desc">${e.message}</div></div>`;
                } finally {
                    btnN.disabled = false;
                    btnA.disabled = false;
                    select.disabled = false;
                }
            }

            window.onload = init;
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

if __name__ == '__main__':
    import uvicorn
    print("=========================================================")
    print("  API Vote-Route Demo — Browser GUI Presentation Runner")
    print("=========================================================")
    print("Launching UI Presentation Demo on http://127.0.0.1:8000 ...")
    uvicorn.run("api_presentation_demo:demo_app", host="127.0.0.1", port=8000, reload=False)

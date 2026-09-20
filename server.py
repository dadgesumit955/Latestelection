import json
import os
import secrets
import threading
from datetime import datetime

import pymongo
from flask import Flask, Response, request, send_file
from pymongo.errors import DuplicateKeyError

MONGODB_URI = os.environ.get(
    "MONGODB_URI",
    "mongodb+srv://krishu2415:6cl0D3k1tG4YdP7y@cluster0.9woa6.mongodb.net/?retryWrites=true",
)
DB_NAME = "election_db"
PORT = int(os.environ.get("PORT", "8000"))
HOST = os.environ.get("HOST", "127.0.0.1")
if os.environ.get("RENDER"):
    HOST = "0.0.0.0"

COLLEGE_NAME = "College of Engineering"
DEPARTMENTS = ["AI & ML", "Computer Engineering", "Civil Engineering", "Electronics & Telecommunication"]
ADMIN_USERS = {"admin": "admin123"}
STATUSES = {"draft", "open", "paused", "closed", "published"}

tokens = {}
uploads = {}
write_lock = threading.Lock()
_db_lock = threading.Lock()
db = None

app = Flask(__name__)

# ---- collections -----------------------------------------------------------
C_STUDENTS = "students"
C_ELECTIONS = "elections"
C_POSITIONS = "positions"
C_CANDIDATES = "candidates"
C_VOTER_STATUS = "voter_status"
C_BALLOTS = "ballots"
C_AUDIT = "audit_log"
C_SEQ = "seq"


def now():
    return datetime.now().isoformat(timespec="seconds")


def make_id(prefix):
    return prefix + secrets.token_hex(4)


def make_pin():
    return str(secrets.randbelow(900000) + 100000)


def next_seq(key):
    doc = db.seq.find_one_and_update(
        {"_id": key},
        {"$inc": {"n": 1}},
        upsert=True,
        return_document=pymongo.ReturnDocument.AFTER,
    )
    return doc["n"]


def init_db():
    global db
    db = pymongo.MongoClient(
        MONGODB_URI,
        serverSelectionTimeoutMS=20000,
        retryWrites=True,
    )[DB_NAME]

    db[C_VOTER_STATUS].create_index(
        [("election_id", 1), ("student_id", 1)], unique=True
    )
    db[C_BALLOTS].create_index(
        [("ballot_id", 1), ("election_id", 1), ("position_id", 1)], unique=True
    )
    db[C_BALLOTS].create_index([("election_id", 1), ("position_id", 1)])
    db[C_BALLOTS].create_index([("election_id", 1), ("position_id", 1), ("candidate_id", 1)])
    db[C_POSITIONS].create_index([("election_id", 1), ("ord", 1)])
    db[C_CANDIDATES].create_index([("position_id", 1), ("ord", 1)])

    if db[C_ELECTIONS].count_documents({}) == 0:
        seed()
        audit("System initialized with demo data")


def _ensure_db():
    global db
    if db is not None:
        return db
    with _db_lock:
        if db is None:
            init_db()
            heal_demo_election()
    return db


def heal_demo_election():
    rows = list(db[C_ELECTIONS].find().sort("created_at", 1))
    if len(rows) == 1 and rows[0]["title"] == "General Secretary & College President Election 2026":
        eid = rows[0]["_id"]
        ballots = db[C_BALLOTS].distinct("ballot_id", {"election_id": eid})
        if not ballots and rows[0]["status"] != "open":
            db[C_VOTER_STATUS].delete_many({"election_id": eid})
            db[C_ELECTIONS].update_one({"_id": eid}, {"$set": {"status": "open"}})
            print("  [self-heal] Demo election reopened for voting (no ballots were cast).")


def audit(action):
    db[C_AUDIT].insert_one({"id": next_seq("audit"), "at": now(), "action": action})


def seed():
    students = [
        ("AIML_01", "Suraj Jadhav", "AI & ML", "SE", "482913"),
        ("AIML_62", "Aditya Pawar", "AI & ML", "SE", "773102"),
        ("CIVIL_03", "Aditya Patil", "Civil Engineering", "SE", "990481"),
        ("CSE_07", "Priya Sharma", "Computer Engineering", "TE", "216907"),
        ("CSE_12", "Rohan Verma", "Computer Engineering", "SE", "548320"),
        ("ETC_04", "Sneha Kulkarni", "Electronics & Telecommunication", "BE", "631845"),
        ("CIVIL_09", "Varun Deshmukh", "Civil Engineering", "BE", "118276"),
        ("ETC_15", "Kavya Nair", "Electronics & Telecommunication", "TE", "905614"),
    ]
    for sid, name, dept, year, pin in students:
        db[C_STUDENTS].update_one(
            {"_id": sid},
            {"$setOnInsert": {"_id": sid, "name": name, "department": dept, "year": year, "pin": pin}},
            upsert=True,
        )

    eid = make_id("e_")
    db[C_ELECTIONS].insert_one({
        "_id": eid,
        "title": "General Secretary & College President Election 2026",
        "type": "college-wide",
        "status": "open",
        "created_at": now(),
        "departments": DEPARTMENTS,
    })

    positions = [
        ("General Secretary", "Manages council coordination, communications and documentation across all departments.", [
            ("Suraj Jadhav", "AI & ML", "SE", "Digital documentation, transparent records, student newsletter", "⚙️"),
            ("Sneha Kulkarni", "Electronics & Telecommunication", "BE", "Streamlined communication, event coordination, council accountability", "🛰️"),
            ("Aditya Patil", "Civil Engineering", "SE", "Organized record systems, health & safety bulletins, campus outreach", "🏗️"),
        ]),
        ("College President", "Represents all engineering students and heads the student council.", [
            ("Priya Sharma", "Computer Engineering", "TE", "Tech literacy programs, career placement, campus innovation hub", "💻"),
            ("Rohan Verma", "Computer Engineering", "SE", "Scholarship expansion, student welfare, inclusive governance", "🎯"),
            ("Kavya Nair", "Electronics & Telecommunication", "TE", "Mental health programs, campus-wide Wi-Fi, sustainability drive", "🌐"),
        ]),
        ("Sports & Cultural Coordinator", "Organizes inter-department sports, cultural festivals and student events.", [
            ("Varun Deshmukh", "Civil Engineering", "BE", "Annual sports meet, inter-department tournaments, fitness initiatives", "🏆"),
            ("Aditya Pawar", "AI & ML", "SE", "Cultural fest, arts funding, student creative spaces", "🎭"),
        ]),
    ]
    for ord_i, (title, desc, cands) in enumerate(positions):
        pid = make_id("p_")
        db[C_POSITIONS].insert_one({"_id": pid, "election_id": eid, "title": title, "description": desc, "ord": ord_i})
        for ord_j, (name, dept, year, platform, symbol) in enumerate(cands):
            db[C_CANDIDATES].insert_one({
                "_id": make_id("c_"), "position_id": pid, "name": name,
                "department": dept, "year": year, "platform": platform,
                "symbol": symbol, "photo": None, "ord": ord_j,
            })


def student_eligible(student, election):
    if election.get("type") == "college-wide":
        return True
    depts = election.get("departments") or []
    if not depts:
        return True
    return student["department"] in depts


def compute_eligible_count(election):
    if election.get("type") == "college-wide":
        return db[C_STUDENTS].count_documents({})
    depts = election.get("departments") or []
    if not depts:
        return db[C_STUDENTS].count_documents({})
    return db[C_STUDENTS].count_documents({"department": {"$in": depts}})


def compute_results(election_id):
    election = db[C_ELECTIONS].find_one({"_id": election_id})
    if not election:
        return None
    positions = list(db[C_POSITIONS].find({"election_id": election_id}).sort("ord", 1))
    eligible = compute_eligible_count(election)
    votes = len(db[C_BALLOTS].distinct("ballot_id", {"election_id": election_id}))
    turnout = round((votes / eligible) * 100) if eligible > 0 else 0

    out_positions = []
    for p in positions:
        cand_rows = list(db[C_CANDIDATES].find({"position_id": p["_id"]}).sort("ord", 1))
        cand_ids = [c["_id"] for c in cand_rows]
        tally = {}
        for c in cand_rows:
            tally[c["_id"]] = db[C_BALLOTS].count_documents(
                {"election_id": election_id, "position_id": p["_id"], "candidate_id": c["_id"]}
            )
        tally["NOTA"] = db[C_BALLOTS].count_documents(
            {"election_id": election_id, "position_id": p["_id"], "candidate_id": "NOTA"}
        )
        if cand_ids:
            orphan = db[C_BALLOTS].count_documents(
                {"election_id": election_id, "position_id": p["_id"],
                 "candidate_id": {"$nin": cand_ids + ["NOTA"]}}
            )
        else:
            orphan = db[C_BALLOTS].count_documents(
                {"election_id": election_id, "position_id": p["_id"], "candidate_id": {"$ne": "NOTA"}}
            )
        tally_vals = list(tally.values()) + [orphan]
        position_total = sum(tally_vals)
        max_votes = max(tally_vals) if tally_vals else 0
        rows = []
        for c in cand_rows:
            v = tally[c["_id"]]
            rows.append({
                "id": c["_id"],
                "name": c["name"],
                "department": c.get("department"),
                "photo": c.get("photo"),
                "symbol": c.get("symbol"),
                "votes": v,
                "pct": round((v / position_total) * 100) if position_total > 0 else 0,
                "winner": v == max_votes and v > 0,
            })
        nota_v = tally["NOTA"]
        rows.append({
            "id": "NOTA",
            "name": "NOTA",
            "department": "",
            "photo": None,
            "symbol": None,
            "votes": nota_v,
            "pct": round((nota_v / position_total) * 100) if position_total > 0 else 0,
            "winner": nota_v == max_votes and nota_v > 0,
        })
        if orphan:
            rows.append({
                "id": "REMOVED",
                "name": "Removed candidate",
                "department": "",
                "photo": None,
                "symbol": None,
                "votes": orphan,
                "pct": round((orphan / position_total) * 100) if position_total > 0 else 0,
                "winner": False,
            })
        out_positions.append({
            "id": p["_id"],
            "title": p["title"],
            "description": p.get("description"),
            "total": position_total,
            "rows": rows,
        })

    return {
        "election": {
            "id": election["_id"],
            "title": election["title"],
            "status": election["status"],
            "type": election["type"],
        },
        "stats": {"votes": votes, "eligible": eligible, "turnout": turnout},
        "positions": out_positions,
    }


def build_election_detail(election):
    positions = []
    for p in db[C_POSITIONS].find({"election_id": election["_id"]}).sort("ord", 1):
        candidates = list(db[C_CANDIDATES].find({"position_id": p["_id"]}).sort("ord", 1))
        positions.append({
            "id": p["_id"],
            "title": p["title"],
            "description": p.get("description"),
            "candidates": [
                {"id": c["_id"], "name": c["name"], "department": c.get("department"),
                 "year": c.get("year"), "platform": c.get("platform"), "symbol": c.get("symbol"),
                 "photo": c.get("photo")}
                for c in candidates
            ],
        })
    ballots = len(db[C_BALLOTS].distinct("ballot_id", {"election_id": election["_id"]}))
    return {
        "id": election["_id"],
        "title": election["title"],
        "type": election["type"],
        "status": election["status"],
        "created_at": election["created_at"],
        "departments": election.get("departments") or [],
        "positions": positions,
        "ballots": ballots,
        "eligible_count": compute_eligible_count(election),
    }


def validate_election_payload(data):
    if not data.get("title") or not str(data["title"]).strip():
        return "Election title is required"
    if data.get("type") not in ("college-wide", "department"):
        return "Invalid election type"
    depts = data.get("departments") or []
    if not isinstance(depts, list):
        return "Departments must be a list"
    positions = data.get("positions") or []
    if not isinstance(positions, list) or len(positions) == 0:
        return "Add at least one position"
    for p in positions:
        if not p.get("title") or not str(p["title"]).strip():
            return "Position title required"
    return None


def replace_election_payload(eid, data):
    db[C_ELECTIONS].update_one({"_id": eid}, {"$set": {"departments": data.get("departments") or []}})
    existing_pos = {p["_id"]: p for p in db[C_POSITIONS].find({"election_id": eid})}
    new_pos_ids = []
    for ord_i, p in enumerate(data.get("positions", [])):
        pid = p.get("id")
        if pid and pid in existing_pos:
            db[C_POSITIONS].update_one(
                {"_id": pid},
                {"$set": {"title": p["title"], "description": p.get("description", ""), "ord": ord_i}},
            )
        else:
            pid = make_id("p_")
            db[C_POSITIONS].insert_one({
                "_id": pid, "election_id": eid, "title": p["title"],
                "description": p.get("description", ""), "ord": ord_i,
            })
        new_pos_ids.append(pid)
        existing_cand = {c["_id"]: c for c in db[C_CANDIDATES].find({"position_id": pid})}
        new_cand_ids = []
        for ord_j, c in enumerate(p.get("candidates", [])):
            if not c.get("name") or not str(c["name"]).strip():
                continue
            cid = c.get("id")
            payload = {
                "name": c["name"], "department": c.get("department", ""), "year": c.get("year", ""),
                "platform": c.get("platform", ""), "symbol": c.get("symbol", ""), "photo": c.get("photo"),
                "ord": ord_j,
            }
            if cid and cid in existing_cand:
                db[C_CANDIDATES].update_one({"_id": cid}, {"$set": payload})
                new_cand_ids.append(cid)
            else:
                cid = make_id("c_")
                payload["_id"] = cid
                payload["position_id"] = pid
                db[C_CANDIDATES].insert_one(payload)
                new_cand_ids.append(cid)
        if new_cand_ids:
            db[C_CANDIDATES].delete_many({"position_id": pid, "_id": {"$nin": new_cand_ids}})
        else:
            db[C_CANDIDATES].delete_many({"position_id": pid})
    for oid in set(existing_pos.keys()) - set(new_pos_ids):
        db[C_CANDIDATES].delete_many({"position_id": oid})
        db[C_POSITIONS].delete_one({"_id": oid})


def make_token(role, student_id=None):
    tok = secrets.token_hex(16)
    tokens[tok] = {"role": role, "student_id": student_id}
    return tok


def authorize(role):
    hdr = request.headers.get("Authorization") or ""
    token = hdr.replace("Bearer ", "") if hdr.startswith("Bearer ") else ""
    info = tokens.get(token)
    if not info or info["role"] != role:
        return None
    return info


def _j(code, obj, headers=None):
    resp = Response(
        json.dumps(obj, ensure_ascii=False),
        status=code,
        mimetype="application/json",
    )
    if headers:
        for k, v in headers.items():
            resp.headers[k] = v
    return resp


# ---- routes ----------------------------------------------------------------
@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    resp.headers["Cache-Control"] = "no-store"
    return resp


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _file(name):
    path = os.path.join(BASE_DIR, name)
    if not os.path.isfile(path):
        return _j(404, {"ok": False, "error": "Not found"})
    return send_file(path)


@app.route("/")
def home():
    return _file("index.html")


@app.route("/index.html")
def index_html():
    return _file("index.html")


@app.route("/app.js")
def app_js():
    return _file("app.js")


@app.route("/style.css")
def style_css():
    return _file("style.css")


@app.route("/api/login", methods=["POST"])
def api_login():
    _ensure_db()
    data = request.get_json(silent=True) or {}
    sid = str(data.get("student_id", "")).strip()
    pin = str(data.get("pin", "")).strip()
    student = db[C_STUDENTS].find_one({"_id": sid})
    if not student or student["pin"] != pin or (not sid and not pin):
        return _j(401, {"ok": False, "error": "Invalid Student ID or PIN"})
    tok = make_token("student", student["_id"])
    return _j(200, {"ok": True, "token": tok, "student": {"id": student["_id"], "name": student["name"], "department": student["department"], "year": student["year"]}})


@app.route("/api/admin/login", methods=["POST"])
def api_admin_login():
    _ensure_db()
    data = request.get_json(silent=True) or {}
    u = str(data.get("username", "")).strip()
    p = str(data.get("password", "")).strip()
    if ADMIN_USERS.get(u) != p:
        return _j(401, {"ok": False, "error": "Invalid admin credentials"})
    tok = make_token("admin")
    audit(f'Admin "{u}" logged in')
    return _j(200, {"ok": True, "token": tok})


@app.route("/api/vote", methods=["POST"])
def api_vote():
    _ensure_db()
    info = authorize("student")
    if not info:
        return _j(401, {"ok": False, "error": "Not authorized"})
    data = request.get_json(silent=True) or {}
    election_id = str(data.get("election_id", ""))
    selections = data.get("selections") or {}
    with write_lock:
        election = db[C_ELECTIONS].find_one({"_id": election_id})
        if not election:
            return _j(404, {"ok": False, "error": "Election not found"})
        if election["status"] != "open":
            return _j(409, {"ok": False, "error": "Voting is not open"})
        student = db[C_STUDENTS].find_one({"_id": info["student_id"]})
        if not student:
            return _j(401, {"ok": False, "error": "Student not found"})
        if election["type"] != "college-wide":
            depts = election.get("departments") or []
            if depts and student["department"] not in depts:
                return _j(403, {"ok": False, "error": "You are not eligible for this election"})
        positions = list(db[C_POSITIONS].find({"election_id": election_id}))
        pos_ids = {p["_id"] for p in positions}
        if set(selections.keys()) != pos_ids:
            return _j(400, {"ok": False, "error": "Every position must have exactly one selection"})
        for pid, cid in selections.items():
            if cid != "NOTA":
                okc = db[C_CANDIDATES].find_one({"_id": cid, "position_id": pid})
                if not okc:
                    return _j(400, {"ok": False, "error": "Invalid candidate selection"})
        receipt = "SAE-" + secrets.token_hex(4).upper()
        ts = now()
        try:
            db[C_VOTER_STATUS].insert_one({
                "election_id": election_id, "student_id": info["student_id"],
                "receipt_no": receipt, "voted_at": ts,
            })
        except DuplicateKeyError:
            return _j(409, {"ok": False, "error": "This student has already voted in this election"})
        ballot_id = "b" + secrets.token_hex(5)
        docs = [
            {"ballot_id": ballot_id, "election_id": election_id, "position_id": pid,
             "candidate_id": cid, "voted_at": ts}
            for pid, cid in selections.items()
        ]
        try:
            db[C_BALLOTS].insert_many(docs)
        except Exception:
            db[C_BALLOTS].delete_many({"ballot_id": ballot_id, "election_id": election_id})
            db[C_VOTER_STATUS].delete_one({"election_id": election_id, "student_id": info["student_id"]})
            return _j(500, {"ok": False, "error": "Server error storing ballot"})
        return _j(200, {"ok": True, "receipt": receipt})


@app.route("/api/results/<eid>", methods=["GET"])
def api_results(eid):
    _ensure_db()
    res = compute_results(eid)
    if not res:
        return _j(404, {"ok": False, "error": "Election not found"})
    return _j(200, {"ok": True, **res})


@app.route("/api/my/elections", methods=["GET"])
def api_my_elections():
    _ensure_db()
    info = authorize("student")
    if not info:
        return _j(401, {"ok": False, "error": "Not authorized"})
    student = db[C_STUDENTS].find_one({"_id": info["student_id"]})
    if not student:
        return _j(401, {"ok": False, "error": "Student not found"})
    available, others = [], []
    for e in db[C_ELECTIONS].find().sort([("created_at", 1), ("_id", 1)]):
        voted = db[C_VOTER_STATUS].find_one(
            {"election_id": e["_id"], "student_id": student["_id"]}
        ) is not None
        detail = build_election_detail(e)
        eligible = student_eligible(student, e)
        if e["status"] == "open" and eligible and not voted:
            available.append({**detail, "voted": voted})
        else:
            others.append({
                "id": e["_id"], "title": e["title"], "type": e["type"],
                "status": e["status"], "voted": voted, "eligible": eligible,
            })
    return _j(200, {"ok": True, "available": available, "others": others})


@app.route("/api/admin/elections", methods=["GET", "POST"])
def api_admin_elections():
    _ensure_db()
    if not authorize("admin"):
        return _j(401, {"ok": False, "error": "Not authorized"})
    if request.method == "GET":
        out = []
        for e in db[C_ELECTIONS].find().sort([("created_at", 1), ("_id", 1)]):
            detail = build_election_detail(e)
            out.append({
                "id": e["_id"], "title": e["title"], "type": e["type"],
                "status": e["status"], "created_at": e["created_at"],
                "positions_count": len(detail["positions"]),
                "ballots": detail["ballots"],
                "eligible_count": detail["eligible_count"],
            })
        return _j(200, {"ok": True, "elections": out})
    data = request.get_json(silent=True) or {}
    validator = validate_election_payload(data)
    if validator:
        return _j(400, {"ok": False, "error": validator})
    with write_lock:
        eid = make_id("e_")
        db[C_ELECTIONS].insert_one({
            "_id": eid, "title": data["title"].strip(), "type": data["type"],
            "status": "draft", "created_at": now(), "departments": data.get("departments") or [],
        })
        replace_election_payload(eid, data)
        audit(f'Created election "{data["title"].strip()}"')
    return _j(200, {"ok": True, "id": eid})


@app.route("/api/admin/elections/<eid>", methods=["GET", "PUT", "DELETE"])
def api_admin_election(eid):
    _ensure_db()
    if not authorize("admin"):
        return _j(401, {"ok": False, "error": "Not authorized"})
    if request.method == "GET":
        e = db[C_ELECTIONS].find_one({"_id": eid})
        if not e:
            return _j(404, {"ok": False, "error": "Election not found"})
        return _j(200, {"ok": True, **build_election_detail(e)})
    if request.method == "DELETE":
        with write_lock:
            row = db[C_ELECTIONS].find_one({"_id": eid})
            if not row:
                return _j(404, {"ok": False, "error": "Election not found"})
            pids = [p["_id"] for p in db[C_POSITIONS].find({"election_id": eid})]
            if pids:
                db[C_CANDIDATES].delete_many({"position_id": {"$in": pids}})
                db[C_BALLOTS].delete_many({"position_id": {"$in": pids}})
            db[C_POSITIONS].delete_many({"election_id": eid})
            db[C_BALLOTS].delete_many({"election_id": eid})
            db[C_VOTER_STATUS].delete_many({"election_id": eid})
            db[C_ELECTIONS].delete_one({"_id": eid})
            audit(f'Deleted election "{row["title"]}"')
        return _j(200, {"ok": True})
    data = request.get_json(silent=True) or {}
    with write_lock:
        row = db[C_ELECTIONS].find_one({"_id": eid})
        if not row:
            return _j(404, {"ok": False, "error": "Election not found"})
        title = str(data.get("title", row["title"])).strip() or row["title"]
        validator = validate_election_payload(data)
        if validator:
            return _j(400, {"ok": False, "error": validator})
        db[C_ELECTIONS].update_one({"_id": eid}, {"$set": {"type": data["type"], "title": title}})
        replace_election_payload(eid, data)
        audit(f'Updated election "{title}"')
    return _j(200, {"ok": True})


@app.route("/api/admin/elections/<eid>/status", methods=["POST"])
def api_admin_status(eid):
    _ensure_db()
    if not authorize("admin"):
        return _j(401, {"ok": False, "error": "Not authorized"})
    data = request.get_json(silent=True) or {}
    status = data.get("status")
    if status not in STATUSES:
        return _j(400, {"ok": False, "error": "Invalid status"})
    with write_lock:
        row = db[C_ELECTIONS].find_one({"_id": eid})
        if not row:
            return _j(404, {"ok": False, "error": "Election not found"})
        db[C_ELECTIONS].update_one({"_id": eid}, {"$set": {"status": status}})
        audit(f'Set election "{row["title"]}" status to {status}')
    return _j(200, {"ok": True})


@app.route("/api/admin/students", methods=["GET"])
def api_admin_students():
    _ensure_db()
    if not authorize("admin"):
        return _j(401, {"ok": False, "error": "Not authorized"})
    rows = [
        {"id": s["_id"], "name": s["name"], "department": s["department"],
         "year": s["year"], "pin": s["pin"]}
        for s in db[C_STUDENTS].find().sort("_id", 1)
    ]
    return _j(200, {"ok": True, "students": rows})


@app.route("/api/admin/students/import", methods=["POST"])
def api_admin_students_import():
    _ensure_db()
    if not authorize("admin"):
        return _j(401, {"ok": False, "error": "Not authorized"})
    data = request.get_json(silent=True) or {}
    if not data.get("confirm"):
        records = data.get("records") or []
        seen = set()
        valid, duplicates, invalid = [], [], []
        for r in records:
            sid = str(r.get("id", "")).strip()
            name = str(r.get("name", "")).strip()
            dept = str(r.get("department", "")).strip()
            year = str(r.get("year", "")).strip() or "SE"
            if not sid or not name or not dept:
                invalid.append({"row": r, "reason": "Missing ID, name or department"})
                continue
            if sid in seen:
                duplicates.append(sid)
                continue
            seen.add(sid)
            if db[C_STUDENTS].find_one({"_id": sid}):
                duplicates.append(sid)
                continue
            valid.append({"id": sid, "name": name, "department": dept, "year": year, "pin": make_pin()})
        token = secrets.token_hex(8)
        uploads[token] = {"records": valid, "at": now()}
        return _j(200, {"ok": True, "upload_token": token, "valid": valid, "duplicates": duplicates, "invalid": invalid})
    token = str(data.get("upload_token", ""))
    staged = uploads.pop(token, None)
    if not staged:
        return _j(400, {"ok": False, "error": "Upload session expired. Please try again."})
    with write_lock:
        inserted = 0
        for r in staged["records"]:
            try:
                db[C_STUDENTS].insert_one({"_id": r["id"], "name": r["name"], "department": r["department"], "year": r["year"], "pin": r["pin"]})
                inserted += 1
            except DuplicateKeyError:
                pass
        audit(f"Imported {inserted} students")
    return _j(200, {"ok": True, "inserted": inserted})


@app.route("/api/admin/students/<sid>", methods=["DELETE"])
def api_admin_student_delete(sid):
    _ensure_db()
    if not authorize("admin"):
        return _j(401, {"ok": False, "error": "Not authorized"})
    with write_lock:
        db[C_VOTER_STATUS].delete_many({"student_id": sid})
        db[C_STUDENTS].delete_one({"_id": sid})
        audit(f'Removed student "{sid}"')
    return _j(200, {"ok": True})


@app.route("/api/admin/audit", methods=["GET", "DELETE"])
def api_admin_audit():
    _ensure_db()
    if not authorize("admin"):
        return _j(401, {"ok": False, "error": "Not authorized"})
    if request.method == "GET":
        log = [
            {"id": a["id"], "at": a["at"], "action": a["action"]}
            for a in db[C_AUDIT].find().sort("id", -1).limit(300)
        ]
        return _j(200, {"ok": True, "log": log})
    with write_lock:
        db[C_AUDIT].delete_many({})
    return _j(200, {"ok": True})


@app.route("/api/admin/export/<eid>", methods=["GET"])
def api_admin_export(eid):
    _ensure_db()
    if not authorize("admin"):
        return _j(401, {"ok": False, "error": "Not authorized"})
    res = compute_results(eid)
    if not res:
        return _j(404, {"ok": False, "error": "Election not found"})
    csv = "Position,Candidate,Votes,Percentage\n"
    for pos in res["positions"]:
        for row in pos["rows"]:
            csv += f'{pos["title"]},{row["name"]},{row["votes"]},{row["pct"]}%\n'
    return _j(200, csv, headers={"Content-Type": "text/csv; charset=utf-8"})


@app.errorhandler(404)
def not_found(_e):
    return _j(404, {"ok": False, "error": "Not found"})


@app.errorhandler(405)
def method_not_allowed(_e):
    return _j(405, {"ok": False, "error": "Method not allowed"})


if __name__ == "__main__":
    print("=" * 56)
    print("  Student Association Election System")
    print("  Database : MongoDB Atlas (%s)" % DB_NAME)
    print("  URL      : http://localhost:%d" % PORT)
    print("  Admin    : admin / admin123")
    print("  Demo voters (ID / PIN):")
    print("    AIML_01 / 482913   | CSE_07 / 216907")
    print("    CSE_12 / 548320    | ETC_15 / 905614")
    print("  Press Ctrl+C to stop.")
    print("=" * 56)
    try:
        app.run(host=HOST, port=PORT, threaded=True)
    except OSError as exc:
        if "address already in use" in str(exc).lower() or "winerror 10048" in str(exc).lower():
            print("  Port %d is already in use - the server appears to already be running." % PORT)
            print("  Just open http://localhost:%d in your browser." % PORT)
        else:
            raise
        raise SystemExit(0)
import json
import os
import secrets
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

import pymongo
from pymongo.errors import DuplicateKeyError

MONGODB_URI = os.environ.get(
    "MONGODB_URI",
    "mongodb+srv://dadgesumit955_db_user:TWB2XaB7sMhA8t0N@cluster0.m8rsdz4.mongodb.net/?retryWrites=true",
)
DB_NAME = "election_db"
PORT = int(os.environ.get("PORT", "8000"))
HOST = os.environ.get("HOST", "127.0.0.1")
if os.environ.get("RENDER"):
    HOST = "0.0.0.0"

COLLEGE_NAME = "College of Engineering"
DEPARTMENTS = ["AI & ML", "Computer Engineering", "Civil Engineering", "Electronics & Telecommunication"]
ADMIN_USERS = {"admin": "jspm123"}
STATUSES = {"draft", "open", "paused", "closed", "published"}

tokens = {}
uploads = {}
write_lock = threading.Lock()

client = None
db = None


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


# ---- collections -----------------------------------------------------------
C_STUDENTS = "students"
C_ELECTIONS = "elections"
C_POSITIONS = "positions"
C_CANDIDATES = "candidates"
C_VOTER_STATUS = "voter_status"
C_BALLOTS = "ballots"
C_AUDIT = "audit_log"
C_SEQ = "seq"


def init_db():
    global client, db
    client = pymongo.MongoClient(
        MONGODB_URI,
        serverSelectionTimeoutMS=20000,
        retryWrites=True,
    )
    db = client[DB_NAME]

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
            ("Suraj Jadhav", "AI & ML", "SE", "Digital documentation, transparent records, student newsletter", "âš™ï¸"),
            ("Sneha Kulkarni", "Electronics & Telecommunication", "BE", "Streamlined communication, event coordination, council accountability", "ðŸ›°ï¸"),
            ("Aditya Patil", "Civil Engineering", "SE", "Organized record systems, health & safety bulletins, campus outreach", "ðŸ—ï¸"),
        ]),
        ("College President", "Represents all engineering students and heads the student council.", [
            ("Priya Sharma", "Computer Engineering", "TE", "Tech literacy programs, career placement, campus innovation hub", "ðŸ’»"),
            ("Rohan Verma", "Computer Engineering", "SE", "Scholarship expansion, student welfare, inclusive governance", "ðŸŽ¯"),
            ("Kavya Nair", "Electronics & Telecommunication", "TE", "Mental health programs, campus-wide Wi-Fi, sustainability drive", "ðŸŒ"),
        ]),
        ("Sports & Cultural Coordinator", "Organizes inter-department sports, cultural festivals and student events.", [
            ("Varun Deshmukh", "Civil Engineering", "BE", "Annual sports meet, inter-department tournaments, fitness initiatives", "ðŸ†"),
            ("Aditya Pawar", "AI & ML", "SE", "Cultural fest, arts funding, student creative spaces", "ðŸŽ­"),
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


def authorize(handler, role):
    hdr = handler.headers.get("Authorization") or ""
    token = hdr.replace("Bearer ", "") if hdr.startswith("Bearer ") else ""
    info = tokens.get(token)
    if not info or info["role"] != role:
        return None
    return info


class Handler(BaseHTTPRequestHandler):
    server_version = "SAE/1.0"

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Cache-Control", "no-store")

    def _json(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _text(self, code, text, ctype="text/plain; charset=utf-8"):
        data = text.encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path):
        if not os.path.isfile(path):
            self._json(404, {"ok": False, "error": "Not found"})
            return
        ctype = "application/octet-stream"
        if path.endswith(".html"):
            ctype = "text/html; charset=utf-8"
        elif path.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        elif path.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            return json.loads(body) if body else {}
        except Exception:
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        parts = [unquote(p) for p in path.split("/") if p]

        if path == "/" or path == "/index.html":
            self._file(os.path.join(BASE_DIR, "index.html"))
            return
        if path in ("/app.js", "/style.css"):
            self._file(os.path.join(BASE_DIR, path.lstrip("/")))
            return

        if parts[:2] == ["api", "results"] and len(parts) == 3:
            res = compute_results(parts[2])
            if not res:
                self._json(404, {"ok": False, "error": "Election not found"})
                return
            self._json(200, {"ok": True, **res})
            return

        if parts[:2] == ["api", "my"] and parts[2] == "elections":
            info = authorize(self, "student")
            if not info:
                self._json(401, {"ok": False, "error": "Not authorized"})
                return
            student = db[C_STUDENTS].find_one({"_id": info["student_id"]})
            if not student:
                self._json(401, {"ok": False, "error": "Student not found"})
                return
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
            self._json(200, {"ok": True, "available": available, "others": others})
            return

        if parts[:2] == ["api", "admin"] and len(parts) >= 3:
            if not authorize(self, "admin"):
                self._json(401, {"ok": False, "error": "Not authorized"})
                return
            if parts[2] == "elections" and len(parts) == 3:
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
                self._json(200, {"ok": True, "elections": out})
                return
            if parts[2] == "elections" and len(parts) == 4:
                e = db[C_ELECTIONS].find_one({"_id": parts[3]})
                if not e:
                    self._json(404, {"ok": False, "error": "Election not found"})
                    return
                self._json(200, {"ok": True, **build_election_detail(e)})
                return
            if parts[2] == "students" and len(parts) == 3:
                rows = [
                    {"id": s["_id"], "name": s["name"], "department": s["department"],
                     "year": s["year"], "pin": s["pin"]}
                    for s in db[C_STUDENTS].find().sort("_id", 1)
                ]
                self._json(200, {"ok": True, "students": rows})
                return
            if parts[2] == "audit" and len(parts) == 3:
                log = [
                    {"id": a["id"], "at": a["at"], "action": a["action"]}
                    for a in db[C_AUDIT].find().sort("id", -1).limit(300)
                ]
                self._json(200, {"ok": True, "log": log})
                return
            if parts[2] == "export" and len(parts) == 4:
                res = compute_results(parts[3])
                if not res:
                    self._json(404, {"ok": False, "error": "Election not found"})
                    return
                csv = "Position,Candidate,Votes,Percentage\n"
                for pos in res["positions"]:
                    for row in pos["rows"]:
                        csv += f'{pos["title"]},{row["name"]},{row["votes"]},{row["pct"]}%\n'
                self._text(200, csv, "text/csv; charset=utf-8")
                return
            self._json(404, {"ok": False, "error": "Unknown endpoint"})
            return

        self._json(404, {"ok": False, "error": "Not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        parts = [unquote(p) for p in path.split("/") if p]
        data = self._json_body()

        if parts == ["api", "login"]:
            sid = str(data.get("student_id", "")).strip()
            pin = str(data.get("pin", "")).strip()
            student = db[C_STUDENTS].find_one({"_id": sid})
            if not student or student["pin"] != pin or (not sid and not pin):
                self._json(401, {"ok": False, "error": "Invalid Student ID or PIN"})
                return
            tok = make_token("student", student["_id"])
            self._json(200, {"ok": True, "token": tok, "student": {"id": student["_id"], "name": student["name"], "department": student["department"], "year": student["year"]}})
            return

        if parts == ["api", "admin", "login"]:
            u = str(data.get("username", "")).strip()
            p = str(data.get("password", "")).strip()
            if ADMIN_USERS.get(u) != p:
                self._json(401, {"ok": False, "error": "Invalid admin credentials"})
                return
            tok = make_token("admin")
            audit(f'Admin "{u}" logged in')
            self._json(200, {"ok": True, "token": tok})
            return

        if parts == ["api", "vote"]:
            info = authorize(self, "student")
            if not info:
                self._json(401, {"ok": False, "error": "Not authorized"})
                return
            election_id = str(data.get("election_id", ""))
            selections = data.get("selections") or {}
            with write_lock:
                election = db[C_ELECTIONS].find_one({"_id": election_id})
                if not election:
                    self._json(404, {"ok": False, "error": "Election not found"})
                    return
                if election["status"] != "open":
                    self._json(409, {"ok": False, "error": "Voting is not open"})
                    return
                student = db[C_STUDENTS].find_one({"_id": info["student_id"]})
                if not student:
                    self._json(401, {"ok": False, "error": "Student not found"})
                    return
                if election["type"] != "college-wide":
                    depts = election.get("departments") or []
                    if depts and student["department"] not in depts:
                        self._json(403, {"ok": False, "error": "You are not eligible for this election"})
                        return
                positions = list(db[C_POSITIONS].find({"election_id": election_id}))
                pos_ids = {p["_id"] for p in positions}
                if set(selections.keys()) != pos_ids:
                    self._json(400, {"ok": False, "error": "Every position must have exactly one selection"})
                    return
                for pid, cid in selections.items():
                    if cid != "NOTA":
                        okc = db[C_CANDIDATES].find_one({"_id": cid, "position_id": pid})
                        if not okc:
                            self._json(400, {"ok": False, "error": "Invalid candidate selection"})
                            return
                receipt = "SAE-" + secrets.token_hex(4).upper()
                ts = now()
                try:
                    db[C_VOTER_STATUS].insert_one({
                        "election_id": election_id, "student_id": info["student_id"],
                        "receipt_no": receipt, "voted_at": ts,
                    })
                except DuplicateKeyError:
                    self._json(409, {"ok": False, "error": "This student has already voted in this election"})
                    return
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
                    self._json(500, {"ok": False, "error": "Server error storing ballot"})
                    return
                self._json(200, {"ok": True, "receipt": receipt})
            return

        if parts[:3] == ["api", "admin", "elections"] and len(parts) == 3 and path.count("/") == 3:
            if not authorize(self, "admin"):
                self._json(401, {"ok": False, "error": "Not authorized"})
                return
            validator = validate_election_payload(data)
            if validator:
                self._json(400, {"ok": False, "error": validator})
                return
            with write_lock:
                eid = make_id("e_")
                db[C_ELECTIONS].insert_one({
                    "_id": eid, "title": data["title"].strip(), "type": data["type"],
                    "status": "draft", "created_at": now(), "departments": data.get("departments") or [],
                })
                replace_election_payload(eid, data)
                audit(f'Created election "{data["title"].strip()}"')
            self._json(200, {"ok": True, "id": eid})
            return

        if parts[:3] == ["api", "admin", "elections"] and len(parts) == 5 and parts[4] == "status":
            if not authorize(self, "admin"):
                self._json(401, {"ok": False, "error": "Not authorized"})
                return
            status = data.get("status")
            if status not in STATUSES:
                self._json(400, {"ok": False, "error": "Invalid status"})
                return
            with write_lock:
                row = db[C_ELECTIONS].find_one({"_id": parts[3]})
                if not row:
                    self._json(404, {"ok": False, "error": "Election not found"})
                    return
                db[C_ELECTIONS].update_one({"_id": parts[3]}, {"$set": {"status": status}})
                audit(f'Set election "{row["title"]}" status to {status}')
            self._json(200, {"ok": True})
            return

        if parts[:3] == ["api", "admin", "students"] and len(parts) == 3:
            if not authorize(self, "admin"):
                self._json(401, {"ok": False, "error": "Not authorized"})
                return
            sid = str(data.get("id", "")).strip()
            name = str(data.get("name", "")).strip()
            dept = str(data.get("department", "")).strip()
            year = str(data.get("year", "")).strip() or "SE"
            if not sid or not name or not dept:
                self._json(400, {"ok": False, "error": "Student ID, name and department are required"})
                return
            with write_lock:
                if db[C_STUDENTS].find_one({"_id": sid}):
                    self._json(409, {"ok": False, "error": f"Student {sid} already exists"})
                    return
                pin = make_pin()
                db[C_STUDENTS].insert_one({"_id": sid, "name": name, "department": dept, "year": year, "pin": pin})
                audit(f'Added student "{sid}"')
            self._json(200, {"ok": True, "student": {"id": sid, "name": name, "department": dept, "year": year, "pin": pin}})
            return

        if parts[:3] == ["api", "admin", "students"] and len(parts) == 4 and parts[3] == "import":
            if not authorize(self, "admin"):
                self._json(401, {"ok": False, "error": "Not authorized"})
                return
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
                self._json(200, {"ok": True, "upload_token": token, "valid": valid, "duplicates": duplicates, "invalid": invalid})
                return
            token = str(data.get("upload_token", ""))
            staged = uploads.pop(token, None)
            if not staged:
                self._json(400, {"ok": False, "error": "Upload session expired. Please try again."})
                return
            with write_lock:
                inserted = 0
                for r in staged["records"]:
                    try:
                        db[C_STUDENTS].insert_one({"_id": r["id"], "name": r["name"], "department": r["department"], "year": r["year"], "pin": r["pin"]})
                        inserted += 1
                    except DuplicateKeyError:
                        pass
                audit(f"Imported {inserted} students")
            self._json(200, {"ok": True, "inserted": inserted})
            return

        self._json(404, {"ok": False, "error": "Unknown endpoint"})

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path
        parts = [unquote(p) for p in path.split("/") if p]
        data = self._json_body()

        if parts[:3] == ["api", "admin", "elections"] and len(parts) == 4:
            if not authorize(self, "admin"):
                self._json(401, {"ok": False, "error": "Not authorized"})
                return
            eid = parts[3]
            with write_lock:
                row = db[C_ELECTIONS].find_one({"_id": eid})
                if not row:
                    self._json(404, {"ok": False, "error": "Election not found"})
                    return
                title = str(data.get("title", row["title"])).strip() or row["title"]
                validator = validate_election_payload(data)
                if validator:
                    self._json(400, {"ok": False, "error": validator})
                    return
                db[C_ELECTIONS].update_one({"_id": eid}, {"$set": {"type": data["type"], "title": title}})
                replace_election_payload(eid, data)
                audit(f'Updated election "{title}"')
            self._json(200, {"ok": True})
            return

        self._json(404, {"ok": False, "error": "Unknown endpoint"})

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        parts = [unquote(p) for p in path.split("/") if p]

        if parts[:3] == ["api", "admin", "elections"] and len(parts) == 4:
            if not authorize(self, "admin"):
                self._json(401, {"ok": False, "error": "Not authorized"})
                return
            eid = parts[3]
            with write_lock:
                row = db[C_ELECTIONS].find_one({"_id": eid})
                if not row:
                    self._json(404, {"ok": False, "error": "Election not found"})
                    return
                pids = [p["_id"] for p in db[C_POSITIONS].find({"election_id": eid})]
                if pids:
                    db[C_CANDIDATES].delete_many({"position_id": {"$in": pids}})
                    db[C_BALLOTS].delete_many({"position_id": {"$in": pids}})
                db[C_POSITIONS].delete_many({"election_id": eid})
                db[C_BALLOTS].delete_many({"election_id": eid})
                db[C_VOTER_STATUS].delete_many({"election_id": eid})
                db[C_ELECTIONS].delete_one({"_id": eid})
                audit(f'Deleted election "{row["title"]}"')
            self._json(200, {"ok": True})
            return

        if parts[:3] == ["api", "admin", "students"] and len(parts) == 4:
            if not authorize(self, "admin"):
                self._json(401, {"ok": False, "error": "Not authorized"})
                return
            sid = parts[3]
            with write_lock:
                db[C_VOTER_STATUS].delete_many({"student_id": sid})
                db[C_STUDENTS].delete_one({"_id": sid})
                audit(f'Removed student "{sid}"')
            self._json(200, {"ok": True})
            return

        if parts[:3] == ["api", "admin", "audit"] and len(parts) == 3:
            if not authorize(self, "admin"):
                self._json(401, {"ok": False, "error": "Not authorized"})
                return
            with write_lock:
                db[C_AUDIT].delete_many({})
            self._json(200, {"ok": True})
            return

        self._json(404, {"ok": False, "error": "Unknown endpoint"})

    def log_message(self, fmt, *args):
        pass


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if __name__ == "__main__":
    init_db()
    heal_demo_election()

    class NoReuseServer(ThreadingHTTPServer):
        allow_reuse_address = False

    try:
        server = NoReuseServer((HOST, PORT), Handler)
    except OSError:
        print("  Port %d is already in use - the server appears to already be running." % PORT)
        print("  Just open http://localhost:%d in your browser." % PORT)
        raise SystemExit(0)
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
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
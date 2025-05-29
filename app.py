from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from functools import wraps
import firebase_admin
from firebase_admin import credentials, firestore
import uuid
from datetime import datetime
import os
import traceback
from fpdf import FPDF

# Initialize Flask app
app = Flask(__name__)
CORS(app, resources={
    r"/*": {
        "origins": ["http://127.0.0.1:5500", "http://localhost:5000"],
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization", "Accept"],
        "expose_headers": ["Content-Type", "Authorization"],
        "supports_credentials": True,
        "max_age": 3600
    }
})

# Firebase Initialization from base64 environment variable
import os
import base64
import json

firebase_creds_b64 = os.environ.get('FIREBASE_CREDENTIALS_BASE64')
if not firebase_creds_b64:
    raise Exception("Missing FIREBASE_CREDENTIALS_BASE64 environment variable")

firebase_creds_json = base64.b64decode(firebase_creds_b64)
cred_dict = json.loads(firebase_creds_json)
cred = credentials.Certificate(cred_dict)
firebase_admin.initialize_app(cred)
db = firestore.client()

# Constants
SECRET_TOKEN = "default_secret_token"  # IMPORTANT: Must match frontend exactly

# Debug logging
@app.before_request
def log_request_info():
    print('Headers:', request.headers)
    print('Body:', request.get_data())
    print('Args:', request.args)

# CORS preflight response
@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization,Accept')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    response.headers.add('Access-Control-Max-Age', '3600')
    return response

# Safe Firestore query wrapper
def safe_firestore_query(query_ref):
    try:
        return query_ref.stream()
    except Exception as e:
        print(f"Firestore query error: {str(e)}")
        return []

# Authentication middleware
# Authentication middleware
# Authentication middleware
def check_authentication(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if request.method == 'OPTIONS':
            return jsonify({"status": "ok"}), 200

        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({
                "error": "Invalid or missing authorization header",
                "received_header": auth_header
            }), 401

        token = auth_header.split(' ')[1]
        if token != SECRET_TOKEN:
            return jsonify({
                "error": "Unauthorized",
                "received_token": token,
                "expected_token": SECRET_TOKEN
            }), 401

        return f(*args, **kwargs)
    return wrapper

@app.before_request
def log_request_info():
    print('Headers:', request.headers)
    print('Authorization:', request.headers.get('Authorization'))
    print('Expected Token:', SECRET_TOKEN)
    print('Body:', request.get_data())
    print('Args:', request.args)

# Health check endpoint
@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "server": "Flask Backend",
        "database": "Firestore"
    }), 200

@app.route("/get_gate_passes", methods=["GET"])
@check_authentication
def get_gate_passes():
    try:
        pass_type = request.args.get('type')

        query = db.collection("gate_pass_requests")
        if pass_type in ['local', 'leave']:
            query = query.where(field_path="pass_type", op_string="==", value=pass_type)

        gate_passes = safe_firestore_query(query)
        result = [{**doc.to_dict(), "id": doc.id} for doc in gate_passes]

        result.sort(key=lambda x: x.get('timestamp', ''), reverse=True)

        return jsonify(result), 200
    except Exception as e:
        error_traceback = traceback.format_exc()
        print(f"Error getting gate passes: {error_traceback}")
        return jsonify({"error": str(e)}), 500

@app.route("/submit_gate_pass", methods=["POST", "OPTIONS"])
def submit_gate_pass():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200

    try:
        data = request.json
        if not data:
            return jsonify({"error": "No data provided"}), 400

        required_fields = [
            "pass_type", "prn_number", "department", "name",
            "wing", "room_number", "reason", "phone_no",
            "proposed_visit", "outing_dates"
        ]

        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing field: {field}"}), 400

        if data["pass_type"] not in ["local", "leave"]:
            return jsonify({"error": "Invalid pass type. Must be 'local' or 'leave'"}), 400

        gate_pass_id = str(uuid.uuid4())

        data.update({
            "timestamp": datetime.now().isoformat(),
            "status": "Pending",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        })

        db.collection("gate_pass_requests").document(gate_pass_id).set(data)
        return jsonify({
            "message": "Gate Pass Submitted",
            "id": gate_pass_id,
            "type": data["pass_type"]
        }), 201

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/get_gate_pass_status/<prn_number>", methods=["GET", "OPTIONS"])
def get_gate_pass_status(prn_number):
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200

    try:
        print(f"Searching for PRN: {prn_number}")

        if not prn_number or not str(prn_number).strip():
            return jsonify({"error": "Invalid PRN number"}), 400

        query = db.collection("gate_pass_requests").where(field_path="prn_number", op_string="==", value=str(prn_number))
        query = query.order_by("timestamp", direction=firestore.Query.DESCENDING)

        print(f"Executing query for PRN: {prn_number}")

        gate_pass_list = []
        try:
            for doc in safe_firestore_query(query):
                data = doc.to_dict()
                data['id'] = doc.id
                gate_pass_list.append(data)
                print(f"Found document: {data}")
        except Exception as doc_error:
            print(f"Error processing document: {str(doc_error)}")

        if not gate_pass_list:
            print(f"No gate passes found for PRN: {prn_number}")
            return jsonify([]), 200

        print(f"Returning {len(gate_pass_list)} gate passes")
        return jsonify(gate_pass_list), 200

    except Exception as e:
        error_traceback = traceback.format_exc()
        print(f"Error processing request for PRN {prn_number}:")
        print(error_traceback)
        return jsonify({
            "error": "Internal server error",
            "message": str(e),
            "details": error_traceback
        }), 500

@app.route("/update_gate_pass/<gate_pass_id>", methods=["POST", "OPTIONS"])
@check_authentication
def update_gate_pass(gate_pass_id):
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200

    try:
        data = request.json
        status = data.get("status")
        reason = data.get("reason", "")

        if status not in ["Approved", "Rejected"]:
            return jsonify({"error": "Invalid status"}), 400

        doc_ref = db.collection("gate_pass_requests").document(gate_pass_id)
        doc = doc_ref.get()

        if not doc.exists:
            return jsonify({"error": "Gate pass not found"}), 404

        update_data = {
            "status": status,
            "updated_at": datetime.now().isoformat()
        }

        if status == "Rejected":
            update_data["rejection_reason"] = reason

        doc_ref.update(update_data)
        return jsonify({
            "message": f"Gate Pass {status}",
            "id": gate_pass_id,
            "updated_at": datetime.now().isoformat()
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/watchmen/gate_passes", methods=["GET"])
@check_authentication
def get_gate_passes_preview():
    try:
        pass_type = request.args.get('type')

        query = db.collection("gate_pass_requests")
        if pass_type in ['local', 'leave']:
            query = query.where(field_path="pass_type", op_string="==", value=pass_type)

        gate_passes = safe_firestore_query(query)
        result = [
            {
                "id": doc.id,
                "name": doc.to_dict().get("name", ""),
                "prn_number": doc.to_dict().get("prn_number", ""),
                "department": doc.to_dict().get("department", ""),
                "wing": doc.to_dict().get("wing", ""),
                "status": doc.to_dict().get("status", ""),
                "pass_type": doc.to_dict().get("pass_type", ""),
                "created_at": doc.to_dict().get("created_at", ""),
                "updated_at": doc.to_dict().get("updated_at", "")
            }
            for doc in gate_passes
        ]
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/watchmen/gate_pass/<gate_pass_id>", methods=["GET", "OPTIONS"])
@check_authentication
def get_gate_pass_details(gate_pass_id):
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200

    try:
        doc_ref = db.collection("gate_pass_requests").document(gate_pass_id)
        doc = doc_ref.get()

        if not doc.exists:
            return jsonify({"error": "Gate pass not found"}), 404

        data = doc.to_dict()
        data['id'] = gate_pass_id
        return jsonify(data), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/download_pdf/<gate_pass_id>", methods=["GET", "OPTIONS"])
@check_authentication
def download_pdf(gate_pass_id):
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200

    try:
        doc_ref = db.collection("gate_pass_requests").document(gate_pass_id)
        doc = doc_ref.get()

        if not doc.exists:
            return jsonify({"error": "Gate pass not found"}), 404

        gate_pass_data = doc.to_dict()

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()

        # Header with institution name
        pdf.set_font("Arial", "B", 16)
        pdf.cell(200, 10, txt="P.V.P.I.T BUDHGAON", ln=True, align="C")

        # Make pass type more prominent
        pdf.set_font("Arial", "B", 14)
        pass_type = gate_pass_data['pass_type'].upper()

        # Add color based on pass type
        if pass_type == "LOCAL":
            pdf.set_fill_color(135, 206, 250)  # Light blue for local
        else:
            pdf.set_fill_color(144, 238, 144)  # Light green for leave

        pdf.cell(200, 10, txt=f"{pass_type} GATE PASS", ln=True, align="C", fill=True)
        pdf.ln(5)

        pdf.set_font("Arial", "", 12)
        details = [
            ("Gate Pass ID", gate_pass_id),
            ("Pass Type", gate_pass_data['pass_type'].upper()),
            ("Name", gate_pass_data['name']),
            ("PRN Number", gate_pass_data['prn_number']),
            ("Department", gate_pass_data['department']),
            ("Wing", gate_pass_data['wing']),
            ("Room Number", gate_pass_data['room_number']),
            ("Reason", gate_pass_data['reason']),
            ("Proposed Visit", gate_pass_data['proposed_visit']),
            ("Outing Dates", gate_pass_data['outing_dates']),
            ("Phone Number", gate_pass_data['phone_no']),
            ("Status", gate_pass_data['status']),
            ("Created At", gate_pass_data.get('created_at', 'N/A')),
            ("Last Updated", gate_pass_data.get('updated_at', 'N/A'))
        ]

        for label, value in details:
            pdf.cell(200, 10, txt=f"{label}: {value}", ln=True)

        if gate_pass_data.get("status") == "Rejected":
            pdf.ln(5)
            pdf.set_font("Arial", "B", 12)
            pdf.cell(200, 10, txt="Rejection Reason:", ln=True)
            pdf.set_font("Arial", "", 12)
            pdf.multi_cell(0, 10, txt=gate_pass_data.get('rejection_reason', 'No reason provided'))

        pdf.ln(20)
        pdf.cell(95, 10, txt="Student's Signature", ln=0, align="C")
        pdf.cell(95, 10, txt="Authority's Signature", ln=True, align="C")

        pdf.ln(10)
        pdf.set_font("Arial", "", 8)
        pdf.cell(200, 5, txt="Officially Verified by P.V.P.I.T. BUDHGAON", ln=True, align="R")
        pdf.cell(200, 5, txt=f"Generated on: {datetime.now().isoformat()}", ln=True, align="R")

        pdf_output_path = f"gate_pass_{gate_pass_id}.pdf"
        pdf.output(pdf_output_path)

        return send_file(pdf_output_path, as_attachment=True)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/get_statistics", methods=["GET", "OPTIONS"])
@check_authentication
def get_statistics():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200

    try:
        stats = {
            "total": {"all": 0, "pending": 0, "approved": 0, "rejected": 0},
            "local": {"all": 0, "pending": 0, "approved": 0, "rejected": 0},
            "leave": {"all": 0, "pending": 0, "approved": 0, "rejected": 0}
        }

        gate_passes = safe_firestore_query(db.collection("gate_pass_requests"))

        for doc in gate_passes:
            pass_data = doc.to_dict()
            pass_type = pass_data.get("pass_type", "unknown")
            status = pass_data.get("status", "unknown").lower()

            if pass_type in ["local", "leave"]:
                stats[pass_type]["all"] += 1
                stats["total"]["all"] += 1

                if status in ["pending", "approved", "rejected"]:
                    stats[pass_type][status] += 1
                    stats["total"][status] += 1

        return jsonify({
            "stats": stats,
            "last_updated": datetime.now().isoformat()
        }), 200

    except Exception as e:
        error_traceback = traceback.format_exc()
        print(f"Error getting statistics: {error_traceback}")
        return jsonify({"error": str(e)}), 500

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Internal server error"}), 500

if __name__ == "__main__":
    app.run(debug=True)

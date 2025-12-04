from flask import Blueprint, send_file, request, jsonify
import pandas as pd
from io import BytesIO
import io
import os
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
import mysql.connector
from werkzeug.utils import secure_filename
from fpdf import FPDF  # pip install fpdf
import traceback

# -----------------------------
# DB connection
# -----------------------------
db = mysql.connector.connect(
    host="localhost",
    user="root",
    password="",
    database="HNU-exam",
    port=3309
)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

api_routes = Blueprint("api_routes", __name__)

@api_routes.route("/api/hello")
def hello():
    return jsonify({"message": "Hello from Flask backend!"})

# =============================
# Helpers (sorting & filters)
# =============================

DAY_FIELD_SQL = "FIELD(e.day,'Saturday','Sunday','Monday','Tuesday','Wednesday','Thursday','Friday')"

PERIOD_MINUTES_SQL = """
(
  (CASE 
     WHEN CAST(SUBSTRING_INDEX(SUBSTRING_INDEX(e.period_id, '-', 1), ':', 1) AS UNSIGNED) BETWEEN 1 AND 7
       THEN CAST(SUBSTRING_INDEX(SUBSTRING_INDEX(e.period_id, '-', 1), ':', 1) AS UNSIGNED) + 12
     ELSE CAST(SUBSTRING_INDEX(SUBSTRING_INDEX(e.period_id, '-', 1), ':', 1) AS UNSIGNED)
   END) * 60
   +
   CAST(SUBSTRING_INDEX(SUBSTRING_INDEX(e.period_id, '-', 1), ':', -1) AS UNSIGNED)
)
"""

def _build_exam_filters(prefix="e."):
    """Build WHERE and params from common query params."""
    filters, params = [], []
    def add(col, qp):
        v = request.args.get(qp)
        if v:
            filters.append(f"{prefix}{col} = %s")
            params.append(v)
    add("program", "program")
    add("level", "level")
    add("code_course", "code_course")
    add("day", "day")
    add("type", "type")
    add("period_id", "period_id")
    add("date", "date")
    v = request.args.get("exam_id")
    if v:
        filters.append(f"{prefix}Exam_id = %s")
        params.append(v)
    where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
    return where_sql, params

# =============================
# DOCTORS (upload)
# =============================

@api_routes.route("/api/v1/doctors/upload", methods=["GET"])
def download_doctor_template():
    columns = ["doctor_name", "email", "phone"]
    df = pd.DataFrame(columns=columns)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name="DoctorsTemplate")
    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name="doctor_template.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

@api_routes.route("/api/v1/doctors/upload", methods=["POST"])
def upload_doctor_file():
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    cursor = None

    try:
        file.save(filepath)
        df = pd.read_excel(filepath)
        required_columns = {"doctor_name", "email", "phone"}
        if not required_columns.issubset(df.columns):
            missing = required_columns - set(df.columns)
            return jsonify({"error": f"Invalid Excel format. Missing columns: {', '.join(missing)}"}), 400

        cursor = db.cursor()
        batch = []
        for _, row in df.iterrows():
            name = str(row.get("doctor_name", "")).strip()
            email = str(row.get("email", "")).strip()
            phone = str(row.get("phone", "")).strip()
            if not name or not email:
                continue
            batch.append((name, email, phone))

        if batch:
            cursor.executemany("INSERT INTO doctors (doctor_name, email, phone) VALUES (%s, %s, %s)", batch)
            db.commit()
            return jsonify({"message": f"Successfully uploaded {len(batch)} doctor(s)!"}), 201
        return jsonify({"message": "No valid doctor records found to upload."}), 200
    except Exception as e:
        if db.is_connected() and cursor:
            db.rollback()
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if os.path.exists(filepath): os.remove(filepath)

# =============================
# ROOMS (upload + CRUD)
# =============================

@api_routes.route("/api/v1/rooms/upload", methods=["GET"])
def download_room_template():
    columns = ["room_name", "capacity", "floor"]
    df = pd.DataFrame(columns=columns)
    out = BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as w:
        df.to_excel(w, index=False, sheet_name="RoomsTemplate")
    out.seek(0)
    return send_file(out, as_attachment=True, download_name="room_template.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@api_routes.route("/api/v1/rooms/upload", methods=["POST"])
def upload_room_file():
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400
    filename = secure_filename(file.filename)
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    cursor = None
    try:
        file.save(filepath)
        df = pd.read_excel(filepath)
        required = {"room_name", "capacity", "floor"}
        if not required.issubset(df.columns):
            missing = required - set(df.columns)
            return jsonify({"error": f"Invalid Excel format. Missing columns: {', '.join(missing)}"}), 400
        cursor = db.cursor()
        batch = []
        for _, row in df.iterrows():
            room_name = str(row.get("room_name", "")).strip()
            floor = str(row.get("floor", "")).strip()
            try:
                capacity = int(row.get("capacity", 0))
            except (TypeError, ValueError):
                continue
            if not room_name or capacity <= 0:
                continue
            batch.append((room_name, capacity, floor))
        if batch:
            cursor.executemany("INSERT INTO rooms (room_name, capacity, floor) VALUES (%s, %s, %s)", batch)
            db.commit()
            return jsonify({"message": f"Successfully uploaded {len(batch)} room(s)!"}), 201
        return jsonify({"message": "No valid room records found to upload."}), 200
    except Exception as e:
        if db.is_connected() and cursor:
            db.rollback()
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if os.path.exists(filepath): os.remove(filepath)

@api_routes.route("/api/v1/rooms", methods=["GET"])
def get_all_rooms():
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute("""SELECT room_id, room_name, capacity, floor FROM rooms ORDER BY floor ASC""")
        return jsonify(cursor.fetchall()), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()

@api_routes.route("/api/v1/rooms", methods=["POST"])
def add_room():
    try:
        data = request.get_json()
        required = ["room_name", "capacity", "floor"]
        if any(data.get(k) in (None, "") for k in required):
            return jsonify({"error": "Missing fields"}), 400
        cursor = db.cursor()
        cursor.execute("INSERT INTO rooms (room_name, capacity, floor) VALUES (%s, %s, %s)",
                       (data["room_name"], data["capacity"], data["floor"]))
        db.commit()
        return jsonify({"message": "✅ Room added successfully!"}), 201
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()

@api_routes.route("/api/v1/rooms/<int:room_id>", methods=["PUT"])
def update_room(room_id):
    try:
        data = request.get_json()
        fields = ["room_name", "capacity", "floor"]
        sets, vals = [], []
        for f in fields:
            if f in data:
                sets.append(f"{f} = %s")
                vals.append(data[f])
        if not sets:
            return jsonify({"error": "No fields provided for update."}), 400
        vals.append(room_id)
        cursor = db.cursor()
        cursor.execute(f"UPDATE rooms SET {', '.join(sets)} WHERE room_id = %s", vals)
        db.commit()
        if cursor.rowcount == 0:
            return jsonify({"message": "No room found with that ID."}), 404
        return jsonify({"message": "✅ Room updated successfully!"}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()

@api_routes.route("/api/v1/rooms/<int:room_id>", methods=["DELETE"])
def delete_room(room_id):
    try:
        cursor = db.cursor()
        cursor.execute("DELETE FROM rooms WHERE room_id = %s", (room_id,))
        db.commit()
        if cursor.rowcount == 0:
            return jsonify({"message": "No room found with that ID."}), 404
        return jsonify({"message": "🗑️ Room deleted successfully!"}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()

# =============================
# COURSES (upload)
# =============================

@api_routes.route("/api/v1/courses/upload", methods=["GET"])
def download_course_template():
    columns = ["Course_code", "course_name", "course_arab_name",
               "credit_hrs", "prerequisite", "type", "lab"]
    df = pd.DataFrame(columns=columns)
    out = BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as w:
        df.to_excel(w, index=False, sheet_name="CoursesTemplate")
    out.seek(0)
    return send_file(out, as_attachment=True, download_name="course_template.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@api_routes.route("/api/v1/courses/upload", methods=["POST"])
def upload_course_file():
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400
    filename = secure_filename(file.filename)
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    cursor = None
    try:
        file.save(filepath)
        df = pd.read_excel(filepath)
        required = {"Course_code", "course_name", "course_arab_name", "credit_hrs", "type"}
        if not required.issubset(df.columns):
            missing = required - set(df.columns)
            return jsonify({"error": f"Invalid Excel format. Missing required columns: {', '.join(missing)}"}), 400
        cursor = db.cursor()
        batch = []
        for _, row in df.iterrows():
            try:
                credit_hrs = int(row.get("credit_hrs", 0))
            except (ValueError, TypeError):
                continue
            course_code = str(row.get("Course_code", "")).strip()
            course_name = str(row.get("course_name", "")).strip()
            course_arab_name = str(row.get("course_arab_name", "")).strip()
            prerequisite = str(row.get("prerequisite", "")).strip()
            course_type = str(row.get("type", "")).strip()
            lab = str(row.get("lab", "")).strip()
            if not course_code or not course_name or credit_hrs <= 0 or not course_type:
                continue
            batch.append((course_code, course_name, course_arab_name,
                          credit_hrs, prerequisite, course_type, lab))
        if batch:
            cursor.executemany("""
                INSERT INTO courses (
                    Course_code, course_name, course_arab_name,
                    credit_hrs, prerequisite, type, lab
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, batch)
            db.commit()
            return jsonify({"message": f"Successfully uploaded {len(batch)} course(s)!"}), 201
        return jsonify({"message": "No valid course records found to upload."}), 200
    except Exception as e:
        if db.is_connected() and cursor: db.rollback()
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if os.path.exists(filepath): os.remove(filepath)

# =============================
# STUDENTS (upload)
# =============================

@api_routes.route("/api/v1/students/upload", methods=["GET"])
def download_student_template():
    columns = ["student_ID","NID","Arab_name","Eng_name","HNU_email",
               "phone_number","parent_number","address","medical_status"]
    df = pd.DataFrame(columns=columns)
    out = BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as w:
        df.to_excel(w, index=False, sheet_name="StudentsTemplate")
    out.seek(0)
    return send_file(out, as_attachment=True, download_name="student_template.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@api_routes.route("/api/v1/students/upload", methods=["POST"])
def upload_student_file():
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400
    filename = secure_filename(file.filename)
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    cursor = None
    try:
        file.save(filepath)
        df = pd.read_excel(filepath)
        required = {"student_ID", "NID", "Arab_name", "Eng_name", "HNU_email"}
        if not required.issubset(df.columns):
            missing = required - set(df.columns)
            return jsonify({"error": f"Invalid Excel format. Missing required columns: {', '.join(missing)}"}), 400
        cursor = db.cursor()
        batch = []
        for _, row in df.iterrows():
            student_id = str(row.get("student_ID", "")).strip()
            nid = str(row.get("NID", "")).strip()
            arab_name = str(row.get("Arab_name", "")).strip()
            eng_name = str(row.get("Eng_name", "")).strip()
            hnu_email = str(row.get("HNU_email", "")).strip()
            phone = str(row.get("phone_number", "")).strip()
            parent = str(row.get("parent_number", "")).strip()
            address = str(row.get("address", "")).strip()
            medical = str(row.get("medical_status", "")).strip()
            if not student_id or not nid or not arab_name or not eng_name or not hnu_email:
                continue
            batch.append((student_id, nid, arab_name, eng_name, hnu_email, phone, parent, address, medical))
        if batch:
            cursor.executemany("""
                INSERT INTO students (
                    student_ID, NID, Arab_name, Eng_name, HNU_email,
                    phone_number, parent_number, address, medical_status
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, batch)
            db.commit()
            return jsonify({"message": f"Successfully uploaded {len(batch)} student(s)!"}), 201
        return jsonify({"message": "No valid student records found to upload."}), 200
    except Exception as e:
        if db.is_connected() and cursor: db.rollback()
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if os.path.exists(filepath): os.remove(filepath)

# =============================
# REGISTRATION  (upload + CRUD + list)
# =============================
@api_routes.route("/api/v1/Student-registration/upload", methods=["GET"])
def download_registration_template():
    columns = ["NID","level","student_ID","course",
               "student_group","student_name","payment","program"]
    df = pd.DataFrame(columns=columns)
    out = BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as w:
        df.to_excel(w, index=False, sheet_name="RegistrationTemplate")
    out.seek(0)
    return send_file(out, as_attachment=True, download_name="registration_template.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@api_routes.route("/api/v1/Student-registration/upload", methods=["POST"])
def upload_registration_file():
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400
    filename = secure_filename(file.filename)
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    cursor = None
    try:
        file.save(filepath)
        df = pd.read_excel(filepath)
        required = {"NID","level","student_ID","course","student_name"}
        if not required.issubset(df.columns):
            missing = required - set(df.columns)
            return jsonify({"error": f"Invalid Excel format. Missing required columns: {', '.join(missing)}"}), 400
        cursor = db.cursor()
        batch = []
        for _, row in df.iterrows():
            NID = str(row.get("NID","")).strip()
            level = str(row.get("level","")).strip()
            student_ID = str(row.get("student_ID","")).strip()
            course = str(row.get("course","")).strip()
            student_group = str(row.get("student_group","")).strip()
            student_name = str(row.get("student_name","")).strip()
            payment = str(row.get("payment","")).strip()
            notes = str(row.get("notes","")).strip()
            if not NID or not level or not student_ID or not course or not student_name:
                continue
            batch.append((NID, level, student_ID, course, student_group, student_name, payment, notes))
        if batch:
            cursor.executemany("""
                INSERT INTO registration (
                    NID, level, student_ID, course, student_group, student_name, payment, notes
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """, batch)
            db.commit()
            return jsonify({"message": f"✅ Successfully uploaded {len(batch)} registration record(s)!"}), 201
        return jsonify({"message": "No valid registration records found to upload."}), 200
    except Exception as e:
        if db.is_connected() and cursor: db.rollback()
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if os.path.exists(filepath): os.remove(filepath)


@api_routes.route("/api/v1/students-registrations", methods=["GET"])
def get_all_registrations():
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT NID, level, student_ID, course, student_group,
                   student_name, payment, program 
            FROM registration
        """)
        return jsonify(cursor.fetchall()), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()

@api_routes.route("/api/v1/Student-registration", methods=["POST"])
def add_registration():
    try:
        data = request.get_json()
        required = ["NID","level","student_ID","course","student_name"]
        if any(not data.get(k) for k in required):
            return jsonify({"error": f"Missing required fields: {', '.join(required)}"}), 400
        cursor = db.cursor()
        cursor.execute("""
            INSERT INTO registration (
              NID, level, student_ID, course, student_group, student_name, payment, notes
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, (data.get("NID"), data.get("level"), data.get("student_ID"),
              data.get("course"), data.get("student_group"), data.get("student_name"),
              data.get("payment"), data.get("notes")))
        db.commit()
        return jsonify({"message":"✅ Registration record added successfully!"}), 201
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()

@api_routes.route("/api/v1/Student-registration/<student_ID>", methods=["PUT"])
def update_registration(student_ID):
    try:
        data = request.get_json()
        fields = ["NID","level","course","student_group","student_name","payment","notes"]
        sets, vals = [], []
        for f in fields:
            if f in data:
                sets.append(f"{f} = %s")
                vals.append(data[f])
        if not sets:
            return jsonify({"error": "No fields provided for update."}), 400
        vals.append(student_ID)
        cursor = db.cursor()
        cursor.execute(f"UPDATE registration SET {', '.join(sets)} WHERE student_ID = %s", vals)
        db.commit()
        if cursor.rowcount == 0:
            return jsonify({"message": "No registration found with that student_ID."}), 404
        return jsonify({"message":"✅ Registration updated successfully!"}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()

@api_routes.route("/api/v1/Student-registration/<student_ID>", methods=["DELETE"])
def delete_registration(student_ID):
    try:
        cursor = db.cursor()
        cursor.execute("DELETE FROM registration WHERE student_ID = %s", (student_ID,))
        db.commit()
        if cursor.rowcount == 0:
            return jsonify({"message":"No registration found with that student_ID."}), 404
        return jsonify({"message":"🗑️ Registration record deleted successfully!"}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()


# =============================
# EXAMS (upload + CRUD + list)
# =============================

@api_routes.route("/api/v1/exams/upload", methods=["GET"])
def download_exam_template():
    columns = ["Exam_id","year","semester","type","period_id","date",
               "program_code","course_code","day","level"]
    df = pd.DataFrame(columns=columns)
    out = BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as w:
        df.to_excel(w, index=False, sheet_name="ExamsTemplate")
    out.seek(0)
    return send_file(out, as_attachment=True, download_name="exam_template.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@api_routes.route("/api/v1/exams/upload", methods=["POST"])
def upload_exam_file():
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400
    filename = secure_filename(file.filename)
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    cursor = None
    try:
        file.save(filepath)
        df = pd.read_excel(filepath)
        required = {"Exam_id","year","semester","course_code","date","level"}
        if not required.issubset(df.columns):
            missing = required - set(df.columns)
            return jsonify({"error": f"Invalid Excel format. Missing required columns: {', '.join(missing)}"}), 400
        cursor = db.cursor()
        batch = []
        for _, row in df.iterrows():
            exam_id = str(row.get("Exam_id","")).strip()
            year = str(row.get("year","")).strip()
            semester = str(row.get("semester","")).strip()
            exam_type = str(row.get("type","")).strip()
            period_id = str(row.get("period_id","")).strip()
            exam_date = str(row.get("date","")).strip()
            program_code = str(row.get("program_code","")).strip()
            course_code = str(row.get("course_code","")).strip()
            day = str(row.get("day","")).strip()
            level = str(row.get("level","")).strip()
            if not exam_id or not semester or not course_code or not exam_date or len(year) <= 0:
                continue
            batch.append((exam_id, year, semester, exam_type, period_id,
                          exam_date, program_code, course_code, day, level))
        if batch:
            cursor.executemany("""
                INSERT INTO exam (
                    Exam_id, year, semester, type, period_id, date,
                    program, code_course, day, level
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, batch)
            db.commit()
            return jsonify({"message": f"✅ Successfully uploaded {len(batch)} exam record(s)!"}), 201
        return jsonify({"message":"⚠️ No valid exam records found to upload."}), 200
    except Exception as e:
        if db.is_connected() and cursor: db.rollback()
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if os.path.exists(filepath): os.remove(filepath)

@api_routes.route("/api/v1/exams", methods=["GET"])
def get_all_exams():
    """Simple list (not grouped) for generic use."""
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT Exam_id, year, semester, type, period_id, date,
                   program, code_course, day, level
            FROM exam
            ORDER BY date ASC
        """)
        return jsonify(cursor.fetchall()), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()

@api_routes.route("/api/v1/exams", methods=["POST"])
def add_exam():
    try:
        data = request.get_json()
        required = ["Exam_id","year","semester","type","period_id","date","program","code_course","day","level"]
        if any(data.get(k) in (None, "") for k in required):
            return jsonify({"error": f"Missing fields: {', '.join(required)}"}), 400
        cursor = db.cursor()
        cursor.execute("""
            INSERT INTO exam (Exam_id, year, semester, type, period_id, date, program, code_course, day, level)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, tuple(data[k] for k in required))
        db.commit()
        return jsonify({"message":"✅ Exam added successfully!"}), 201
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()

@api_routes.route("/api/v1/exams/<Exam_id>", methods=["PUT"])
def update_exam(Exam_id):
    try:
        data = request.get_json()
        fields = ["year","semester","type","period_id","date","program","code_course","day","level"]
        sets, vals = [], []
        for f in fields:
            if f in data:
                sets.append(f"{f} = %s")
                vals.append(data[f])
        if not sets:
            return jsonify({"error":"No fields provided for update."}), 400
        vals.append(Exam_id)
        cursor = db.cursor()
        cursor.execute(f"UPDATE exam SET {', '.join(sets)} WHERE Exam_id = %s", vals)
        db.commit()
        if cursor.rowcount == 0:
            return jsonify({"message":"No exam found with that ID."}), 404
        return jsonify({"message":"✅ Exam updated successfully!"}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error":str(e)}), 500
    finally:
        cursor.close()

@api_routes.route("/api/v1/exams/<Exam_id>", methods=["DELETE"])
def delete_exam(Exam_id):
    try:
        cursor = db.cursor()
        cursor.execute("DELETE FROM exam WHERE Exam_id = %s", (Exam_id,))
        db.commit()
        if cursor.rowcount == 0:
            return jsonify({"message":"No exam found with that ID."}), 404
        return jsonify({"message":"🗑️ Exam deleted successfully!"}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error":str(e)}), 500
    finally:
        cursor.close()

# =============================
# LEGAN (upload + CRUD list)
# =============================

@api_routes.route("/api/v1/legans/upload", methods=["GET"])
def download_legan_template():
    columns = ["Legan_id","legan_name","room_id","level","capacity","full_capacity","program"]
    df = pd.DataFrame(columns=columns)
    out = BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as w:
        df.to_excel(w, index=False, sheet_name="LegansTemplate")
    out.seek(0)
    return send_file(out, as_attachment=True, download_name="legan_template.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@api_routes.route("/api/v1/legans/upload", methods=["POST"])
def upload_legan_file():
    if "file" not in request.files:
        return jsonify({"error":"No file part"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error":"No file selected"}), 400
    filename = secure_filename(file.filename)
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    cursor = None
    try:
        file.save(filepath)
        df = pd.read_excel(filepath)
        required = {"Legan_id","legan_name","room_id","level","capacity","full_capacity","program"}
        if not required.issubset(df.columns):
            missing = required - set(df.columns)
            return jsonify({"error": f"Invalid Excel format. Missing required columns: {', '.join(missing)}"}), 400
        cursor = db.cursor()
        batch = []
        for _, row in df.iterrows():
            try:
                room_id = int(float(str(row.get("room_id","")).strip() or 0))
                capacity = int(float(str(row.get("capacity","")).strip() or 0))
                full_capacity = int(float(str(row.get("full_capacity","")).strip() or 0))
            except (ValueError, TypeError):
                continue
            Legan_id = str(row.get("Legan_id","")).strip()
            legan_name = str(row.get("legan_name","")).strip()
            level = str(row.get("level","")).strip() or "level 1"
            program = str(row.get("program","")).strip()
            if not Legan_id or not legan_name or not program or (capacity <= 0 and full_capacity <= 0):
                continue
            batch.append((Legan_id, legan_name, room_id, level, capacity, full_capacity, program))
        if batch:
            cursor.executemany("""
                INSERT INTO legan (
                    Legan_id, legan_name, room_id, level, capacity, full_capacity, program
                ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, batch)
            db.commit()
            return jsonify({"message": f"Successfully uploaded {len(batch)} legan record(s)!"}), 201
        return jsonify({"message":"No valid legan records found to upload."}), 200
    except Exception as e:
        if db.is_connected() and cursor: db.rollback()
        traceback.print_exc()
        return jsonify({"error":str(e)}), 500
    finally:
        if cursor: cursor.close()
        if os.path.exists(filepath): os.remove(filepath)

@api_routes.route("/api/v1/legans", methods=["GET"])
def get_all_legans():
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT Legan_id, legan_name, room_id, level, capacity, full_capacity, program
            FROM legan
            ORDER BY level ASC
        """)
        return jsonify(cursor.fetchall()), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error":str(e)}), 500
    finally:
        cursor.close()

@api_routes.route("/api/v1/legans", methods=["POST"])
def add_legan():
    try:
        data = request.get_json()
        required = ["Legan_id","legan_name","room_id","level","capacity","full_capacity","program"]
        if any(data.get(k) in (None, "") for k in required):
            return jsonify({"error": f"Missing fields: {', '.join(required)}"}), 400
        cursor = db.cursor()
        cursor.execute("""
            INSERT INTO legan (Legan_id, legan_name, room_id, level, capacity, full_capacity, program)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
        """, tuple(data[k] for k in required))
        db.commit()
        return jsonify({"message":"✅ Legan added successfully!"}), 201
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error":str(e)}), 500
    finally:
        cursor.close()

@api_routes.route("/api/v1/legans/<Legan_id>", methods=["PUT"])
def update_legan(Legan_id):
    try:
        data = request.get_json()
        fields = ["legan_name","room_id","level","capacity","full_capacity","program"]
        sets, vals = [], []
        for f in fields:
            if f in data:
                sets.append(f"{f} = %s")
                vals.append(data[f])
        if not sets:
            return jsonify({"error":"No fields provided for update."}), 400
        vals.append(Legan_id)
        cursor = db.cursor()
        cursor.execute(f"UPDATE legan SET {', '.join(sets)} WHERE Legan_id = %s", vals)
        db.commit()
        if cursor.rowcount == 0:
            return jsonify({"message":"No legan found with that ID."}), 404
        return jsonify({"message":"✅ Legan updated successfully!"}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error":str(e)}), 500
    finally:
        cursor.close()

@api_routes.route("/api/v1/legans/<Legan_id>", methods=["DELETE"])
def delete_legan(Legan_id):
    try:
        cursor = db.cursor()
        cursor.execute("DELETE FROM legan WHERE Legan_id = %s", (Legan_id,))
        db.commit()
        if cursor.rowcount == 0:
            return jsonify({"message":"No legan found with that ID."}), 404
        return jsonify({"message":"🗑️ Legan deleted successfully!"}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error":str(e)}), 500
    finally:
        cursor.close()

# =============================
# STUDENTS-LEGANS (grouped for UI)
# =============================

# ---------- Optional PDF dependency (fallback to 501 if missing) ----------
try:
    from fpdf import FPDF
    HAS_FPDF = True
except Exception:
    HAS_FPDF = False




# =============================================================================
# Utility: ensure tables/columns exist (run once at startup)
# =============================================================================
def _ensure_schema():
    try:
        cur = db.cursor()
        # exam.assigned (0/1)
        cur.execute("""
            ALTER TABLE exam
            ADD COLUMN IF NOT EXISTS assigned TINYINT(1) NOT NULL DEFAULT 0
        """)
        # student_legan_history (simple log)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS student_legan_history (
                id INT AUTO_INCREMENT PRIMARY KEY,
                legan_id INT NOT NULL,
                student_id VARCHAR(64) NOT NULL,
                exam_id INT NOT NULL,
                action VARCHAR(32) NOT NULL, -- 'ASSIGNED'
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        try:
            cur.close()
        except Exception:
            pass

_ensure_schema()

# =============================================================================
# -----------------------------  STUDENTS–LEGANS  -----------------------------
# =============================================================================
# This section exposes:
# GET  /api/v1/students-legans              -> list exams + grouped legans
# POST /api/v1/students-legans              -> create an exam row
# PUT  /api/v1/students-legans/<Exam_id>    -> update exam row
# DELETE /api/v1/students-legans/<Exam_id>  -> delete exam row
#
# POST /api/v1/assign/<exam_id>             -> auto-assign students to legans
#
# GET  /api/v1/students-legans/print        -> printable JSON (grouped by legan)
# GET  /api/v1/students-legans/print/pdf    -> PDF (501 if FPDF not installed)
# =============================================================================

DAY_ORDER_SQL = ("FIELD(e.day, 'Saturday','Sunday','Monday','Tuesday','Wednesday','Thursday','Friday')")

# Convert "H:MM-H:MM" to minutes (start) inside SQL for sort
PERIOD_MINUTES_SQL = """
(
    (
        CASE
            WHEN CAST(SUBSTRING_INDEX(SUBSTRING_INDEX(e.period_id,'-',1),':',1) AS UNSIGNED) BETWEEN 1 AND 7
                THEN CAST(SUBSTRING_INDEX(SUBSTRING_INDEX(e.period_id,'-',1),':',1) AS UNSIGNED) + 12
            ELSE CAST(SUBSTRING_INDEX(SUBSTRING_INDEX(e.period_id,'-',1),':',1) AS UNSIGNED)
        END
    ) * 60
    +
    CAST(SUBSTRING_INDEX(SUBSTRING_INDEX(e.period_id,'-',1),':',-1) AS UNSIGNED)
)
"""

def _build_exam_filters(alias="e"):
    where, params = [], []
    def add(col, qp):
        val = request.args.get(qp)
        if val:
            where.append(f"{alias}.{col} = %s")
            params.append(val)
    # all are exact filters (frontend sends exact strings)
    add("program", "program")
    add("level", "level")
    add("code_course", "code_course")
    add("day", "day")
    add("type", "type")
    add("period_id", "period_id")
    add("date", "date")
    # specific exam
    exam_id = request.args.get("exam_id")
    if exam_id:
        where.append(f"{alias}.Exam_id = %s")
        params.append(exam_id)
    return where, params

@api_routes.route("/api/v1/students-legans", methods=["GET"])
def get_all_students_legans():
    """
    Return exams WITH grouped legans, plus assigned flag.
    Filters: program, level, code_course, day, type, period_id, date, exam_id
    Sorted: Day (Sat..Fri), Period start, Exam_id
    """
    cursor = None
    try:
        where, params = _build_exam_filters("e")
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""

        sql = f"""
        SELECT
            e.Exam_id, e.year, e.semester, e.type, e.program, e.code_course,
            e.date, e.day, e.level, e.period_id, e.assigned,
            l.legan_id, l.legan_name, l.capacity AS legan_capacity, r.room_name
        FROM exam e
        LEFT JOIN student_legan sl ON sl.Exam = e.Exam_id
        LEFT JOIN legan l ON l.legan_id = sl.legan_id
        LEFT JOIN rooms r ON r.room_id = l.room_id
        {where_sql}
        ORDER BY
            {DAY_ORDER_SQL},
            {PERIOD_MINUTES_SQL},
            e.Exam_id
        """
        cursor = db.cursor(dictionary=True)
        cursor.execute(sql, params)
        rows = cursor.fetchall()

        grouped = {}
        for row in rows:
            eid = row["Exam_id"]
            if eid not in grouped:
                grouped[eid] = {
                    "Exam_id": eid,
                    "year": row["year"],
                    "semester": row["semester"],
                    "type": row["type"],
                    "program": row["program"],
                    "code_course": row["code_course"],
                    "date": row["date"],
                    "day": row["day"],
                    "level": row["level"],
                    "period_id": row["period_id"],
                    "assigned": int(row.get("assigned") or 0),
                    "legans": []
                }
            if row["legan_id"]:
                grouped[eid]["legans"].append({
                    "legan_id": row["legan_id"],
                    "legan_name": row["legan_name"],
                    "room_name": row["room_name"],
                    "capacity": row["legan_capacity"]
                })

        # also include exams that have no legans at all (when filter returns empty left-join)
        if not rows:
            # Try fetch exams only
            cursor.execute(f"""
                SELECT e.Exam_id, e.year, e.semester, e.type, e.program, e.code_course,
                       e.date, e.day, e.level, e.period_id, e.assigned
                FROM exam e
                {where_sql}
                ORDER BY {DAY_ORDER_SQL}, {PERIOD_MINUTES_SQL}, e.Exam_id
            """, params)
            only = cursor.fetchall()
            for row in only:
                grouped[row["Exam_id"]] = {
                    "Exam_id": row["Exam_id"],
                    "year": row["year"],
                    "semester": row["semester"],
                    "type": row["type"],
                    "program": row["program"],
                    "code_course": row["code_course"],
                    "date": row["date"],
                    "day": row["day"],
                    "level": row["level"],
                    "period_id": row["period_id"],
                    "assigned": int(row.get("assigned") or 0),
                    "legans": []
                }

        return jsonify(list(grouped.values())), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            cursor.close()
        except Exception:
            pass

@api_routes.route("/api/v1/students-legans", methods=["POST"])
def add_students_legan():
    """Create an exam row (assigned=0 by default)."""
    cur = None
    try:
        data = request.get_json(force=True)
        required = ["Exam_id", "year", "semester", "type", "period_id", "date", "program", "code_course", "day", "level"]
        miss = [f for f in required if f not in data or data[f] in [None, ""]]
        if miss:
            return jsonify({"error": f"Missing fields: {', '.join(miss)}"}), 400

        sql = """
            INSERT INTO exam (Exam_id, year, semester, type, period_id, date, program, code_course, day, level, assigned)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0)
        """
        vals = tuple(data[f] for f in required)
        cur = db.cursor()
        cur.execute(sql, vals)
        db.commit()
        return jsonify({"message": "✅ Exam created successfully"}), 201
    except Exception as e:
        if db.is_connected() and cur: db.rollback()
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        try: cur.close()
        except Exception: pass

@api_routes.route("/api/v1/students-legans/<int:Exam_id>", methods=["PUT"])
def update_students_legan(Exam_id):
    """Update exam fields; you may also set assigned=0/1 explicitly if you want."""
    cur = None
    try:
        data = request.get_json(force=True)
        fields = ["year","semester","type","period_id","date","program","code_course","day","level","assigned"]
        sets, vals = [], []
        for f in fields:
            if f in data:
                sets.append(f"{f}=%s")
                vals.append(data[f])
        if not sets:
            return jsonify({"error":"No fields provided for update"}), 400
        sql = f"UPDATE exam SET {', '.join(sets)} WHERE Exam_id=%s"
        vals.append(Exam_id)
        cur = db.cursor()
        cur.execute(sql, vals)
        db.commit()
        if cur.rowcount == 0:
            return jsonify({"message":"No record found for that Exam_id"}), 404
        return jsonify({"message":"✅ Exam updated successfully"}), 200
    except Exception as e:
        if db.is_connected() and cur: db.rollback()
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        try: cur.close()
        except Exception: pass

@api_routes.route("/api/v1/students-legans/<int:Exam_id>", methods=["DELETE"])
def delete_students_legan(Exam_id):
    """Delete exam row."""
    cur = None
    try:
        cur = db.cursor()
        cur.execute("DELETE FROM exam WHERE Exam_id=%s", (Exam_id,))
        db.commit()
        if cur.rowcount == 0:
            return jsonify({"message":"No record found with that Exam_id"}), 404
        return jsonify({"message":"🗑️ Exam deleted successfully"}), 200
    except Exception as e:
        if db.is_connected() and cur: db.rollback()
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        try: cur.close()
        except Exception: pass

# ----------------------------- ASSIGNMENT -----------------------------
@api_routes.route("/api/v1/assign/<int:exam_id>", methods=["POST"])
def assign_course(exam_id):
    """
    Assign all students of the exam's course to legans for that program+level.
    - Programs are ALWAYS uppercase in DB (confirmed).
    - Legan capacity is PER-ROW (already filtered by level).
    Steps:
      1) get exam
      2) get legans for same program+level (ORDER BY Legan_id)
      3) get students from registration for same program+course
      4) fill legans by capacity
      5) insert into student_legan, mark exam.assigned=1, write history
    """
    cur = None
    try:
        cur = db.cursor(dictionary=True)

        # 1) exam
        cur.execute("SELECT * FROM exam WHERE Exam_id=%s", (exam_id,))
        exam = cur.fetchone()
        if not exam:
            return jsonify({"error": "Exam not found"}), 404

        program = str(exam["program"]).upper()  # confirmed uppercase in DB, but safe
        level   = str(exam["level"])
        course  = str(exam["code_course"])

        # 2) legans
        cur.execute("""
            SELECT Legan_id, legan_name, room_id, level, capacity, program
            FROM legan
            WHERE program=%s AND level=%s
            ORDER BY Legan_id ASC
        """, (program, level))
        legans = cur.fetchall()
        if not legans:
            return jsonify({"error":"No legans available for this program/level"}), 400

        # 3) students
        # cur.execute("""
        #     SELECT student_ID,  student_name
        #     FROM registration
        #     WHERE program=%s AND course=%s
        #     ORDER BY student_ID
        # """, (program, course))
        # students = cur.fetchall()
        cur.execute("""
            SELECT student_ID, student_name, level
            FROM registration
            WHERE program=%s AND course=%s
            ORDER BY level ASC, student_ID
        """, (program, course))
        students = cur.fetchall()
        if not students:
            return jsonify({"error":"No registered students for this course"}), 400

        # 4) assign
        inserted = 0
        idx = 0
        total = len(students)

        ins_map = db.cursor()   # for inserts (faster)
        history = db.cursor()

        for leg in legans:
            cap = int(leg["capacity"] or 0)
            for _ in range(cap):
                if idx >= total:
                    break
                s = students[idx]
                idx += 1

                ins_map.execute("""
                    INSERT INTO student_legan (legan_id, student_id, Exam)
                    VALUES (%s,%s,%s)
                """, (leg["Legan_id"], s["student_ID"], exam_id))
                history.execute("""
                    INSERT INTO student_legan_history (legan_id, student_id, exam_id, action)
                    VALUES (%s,%s,%s,'ASSIGNED')
                """, (leg["Legan_id"], s["student_ID"], exam_id))
                inserted += 1

            if idx >= total:
                break

        # 5) mark assigned=1 if any assignment happened
        if inserted > 0:
            cur2 = db.cursor()
            cur2.execute("UPDATE exam SET assigned=1 WHERE Exam_id=%s", (exam_id,))
            cur2.close()

        db.commit()
        ins_map.close()
        history.close()

        return jsonify({
            "message": f"✅ Assigned {inserted}/{total} students.",
            "assigned": inserted,
            "total": total
        }), 200

    except Exception as e:
        if db.is_connected() and cur: db.rollback()
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        try: cur.close()
        except Exception: pass
# -----------------------------Reassignment -----------------------------
@api_routes.route("/api/v1/reassign/<int:exam_id>", methods=["POST"])
def reassign_new_students(exam_id):
    """
    Reassign only NEWLY added students for a given exam.
    - Continue filling from the last legan used (if it still has capacity).
    - Keep students in the order they were added (no sorting).
    """

    cur = None
    try:
        cur = db.cursor(dictionary=True)

        # 1) Get exam info
        cur.execute("SELECT * FROM exam WHERE Exam_id=%s", (exam_id,))
        exam = cur.fetchone()
        if not exam:
            return jsonify({"error": "Exam not found"}), 404

        program = str(exam["program"]).upper()
        level = str(exam["level"])
        course = str(exam["code_course"])

        # 2) Get legans (ordered ascending)
        cur.execute("""
            SELECT Legan_id, legan_name, room_id, level, capacity, program
            FROM legan
            WHERE program=%s AND level=%s
            ORDER BY Legan_id ASC
        """, (program, level))
        legans = cur.fetchall()
        if not legans:
            return jsonify({"error": "No legans found for this program/level"}), 400

        # 3) Get all registered students (⚠️ keep insertion order)
        cur.execute("""
            SELECT student_ID, student_name
            FROM registration
            WHERE program=%s AND course=%s
        """, (program, course))
        all_students = cur.fetchall()
        if not all_students:
            return jsonify({"error": "No students registered for this course"}), 400

        # 4) Get already assigned student IDs
        cur.execute("""
            SELECT student_id, legan_id
            FROM student_legan
            WHERE Exam=%s
        """, (exam_id,))
        assigned = cur.fetchall()
        assigned_ids = {row["student_id"] for row in assigned}

        # 5) Filter new students (keep natural order)
        new_students = [s for s in all_students if s["student_ID"] not in assigned_ids]
        if not new_students:
            return jsonify({"message": "✅ No new students found. All already assigned."}), 200

        # 6) Find the last legan used in this exam
        cur.execute("""
            SELECT legan_id
            FROM student_legan
            WHERE Exam=%s
            ORDER BY student_Legan_id DESC
            LIMIT 1
        """, (exam_id,))
        last_used = cur.fetchone()
        last_used_id = last_used["legan_id"] if last_used else None

        # 7) Find index of that legan
        start_index = 0
        if last_used_id:
            for i, leg in enumerate(legans):
                if leg["Legan_id"] == last_used_id:
                    start_index = i
                    break

        ins_map = db.cursor()
        history = db.cursor()
        inserted = 0
        idx = 0
        total_new = len(new_students)

        # Fill from last used legan forward
        for i in range(start_index, len(legans)):
            leg = legans[i]
            cap = int(leg["capacity"] or 0)
            cur.execute("SELECT COUNT(*) AS c FROM student_legan WHERE legan_id=%s AND Exam=%s", (leg["Legan_id"], exam_id))
            used = cur.fetchone()["c"]
            free = max(0, cap - used)

            for _ in range(free):
                if idx >= total_new:
                    break
                s = new_students[idx]
                idx += 1

                ins_map.execute("""
                    INSERT INTO student_legan (legan_id, student_id, Exam)
                    VALUES (%s,%s,%s)
                """, (leg["Legan_id"], s["student_ID"], exam_id))
                history.execute("""
                    INSERT INTO student_legan_history (legan_id, student_id, exam_id, action)
                    VALUES (%s,%s,%s,'REASSIGNED')
                """, (leg["Legan_id"], s["student_ID"], exam_id))
                inserted += 1

            if idx >= total_new:
                break

        # If students remain, wrap to earlier legans
        if idx < total_new:
            for i in range(0, start_index):
                leg = legans[i]
                cap = int(leg["capacity"] or 0)
                cur.execute("SELECT COUNT(*) AS c FROM student_legan WHERE legan_id=%s AND Exam=%s", (leg["Legan_id"], exam_id))
                used = cur.fetchone()["c"]
                free = max(0, cap - used)

                for _ in range(free):
                    if idx >= total_new:
                        break
                    s = new_students[idx]
                    idx += 1

                    ins_map.execute("""
                        INSERT INTO student_legan (legan_id, student_id, Exam)
                        VALUES (%s,%s,%s)
                    """, (leg["Legan_id"], s["student_ID"], exam_id))
                    history.execute("""
                        INSERT INTO student_legan_history (legan_id, student_id, exam_id, action)
                        VALUES (%s,%s,%s,'REASSIGNED')
                    """, (leg["Legan_id"], s["student_ID"], exam_id))
                    inserted += 1

                if idx >= total_new:
                    break

        db.commit()
        ins_map.close()
        history.close()

        return jsonify({
            "message": f"🔄 Reassigned {inserted}/{total_new} new students (kept insertion order).",
            "new_assigned": inserted,
            "new_total": total_new,
            "started_from_legan": last_used_id or legans[0]["Legan_id"]
        }), 200

    except Exception as e:
        if db.is_connected() and cur:
            db.rollback()
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    finally:
        try:
            cur.close()
        except Exception:
            pass

# @api_routes.route("/api/v1/reassign/<int:exam_id>", methods=["POST"])
# def reassign_new_students(exam_id):
#     """
#     Reassign only NEWLY added students for a given exam.
#     Continue filling from the last legan used in previous assignment/reassignment,
#     if it still has remaining capacity.
#     """

#     cur = None
#     try:
#         cur = db.cursor(dictionary=True)

#         # 1) Get exam info
#         cur.execute("SELECT * FROM exam WHERE Exam_id=%s", (exam_id,))
#         exam = cur.fetchone()
#         if not exam:
#             return jsonify({"error": "Exam not found"}), 404

#         program = str(exam["program"]).upper()
#         level = str(exam["level"])
#         course = str(exam["code_course"])

#         # 2) Get legans
#         cur.execute("""
#             SELECT Legan_id, legan_name, room_id, level, capacity, program
#             FROM legan
#             WHERE program=%s AND level=%s
#             ORDER BY Legan_id ASC
#         """, (program, level))
#         legans = cur.fetchall()
#         if not legans:
#             return jsonify({"error": "No legans found for this program/level"}), 400

#         # 3) Get all registered students
#         cur.execute("""
#             SELECT student_ID, student_name
#             FROM registration
#             WHERE program=%s AND course=%s
#             ORDER BY student_ID
#         """, (program, course))
#         all_students = cur.fetchall()
#         if not all_students:
#             return jsonify({"error": "No students registered for this course"}), 400

#         # 4) Get already assigned student IDs
#         cur.execute("""
#             SELECT student_id, legan_id
#             FROM student_legan
#             WHERE Exam=%s
#         """, (exam_id,))
#         assigned = cur.fetchall()
#         assigned_ids = {row["student_id"] for row in assigned}

#         # 5) Filter new students
#         new_students = [s for s in all_students if s["student_ID"] not in assigned_ids]
#         if not new_students:
#             return jsonify({"message": "✅ No new students found. All already assigned."}), 200

#         # 6) Find last used legan
#         cur.execute("""
#             SELECT legan_id
#             FROM student_legan
#             WHERE Exam=%s
#             ORDER BY legan_id DESC
#             LIMIT 1
#         """, (exam_id,))
#         last_used = cur.fetchone()
#         last_used_id = last_used["legan_id"] if last_used else None

#         # Determine starting index in legan list
#         start_index = 0
#         if last_used_id:
#             for i, leg in enumerate(legans):
#                 if leg["Legan_id"] == last_used_id:
#                     start_index = i
#                     break

#         # 7) Prepare for insertion
#         ins_map = db.cursor()
#         history = db.cursor()
#         inserted = 0
#         idx = 0
#         total_new = len(new_students)

#         # Rotate legans list to start from last used one
#         ordered_legans = legans[start_index:] + legans[:start_index]

#         for leg in ordered_legans:
#             cap = int(leg["capacity"] or 0)
#             cur.execute("SELECT COUNT(*) AS c FROM student_legan WHERE legan_id=%s AND Exam=%s", (leg["Legan_id"], exam_id))
#             used = cur.fetchone()["c"]
#             free = max(0, cap - used)

#             for _ in range(free):
#                 if idx >= total_new:
#                     break
#                 s = new_students[idx]
#                 idx += 1

#                 ins_map.execute("""
#                     INSERT INTO student_legan (legan_id, student_id, Exam)
#                     VALUES (%s,%s,%s)
#                 """, (leg["Legan_id"], s["student_ID"], exam_id))
#                 history.execute("""
#                     INSERT INTO student_legan_history (legan_id, student_id, exam_id, action)
#                     VALUES (%s,%s,%s,'REASSIGNED')
#                 """, (leg["Legan_id"], s["student_ID"], exam_id))
#                 inserted += 1

#             if idx >= total_new:
#                 break

#         db.commit()
#         ins_map.close()
#         history.close()

#         return jsonify({
#             "message": f"🔄 Reassigned {inserted}/{total_new} new students, starting from legan {last_used_id or legans[0]['Legan_id']}.",
#             "new_assigned": inserted,
#             "new_total": total_new
#         }), 200

#     except Exception as e:
#         if db.is_connected() and cur:
#             db.rollback()
#         traceback.print_exc()
#         return jsonify({"error": str(e)}), 500

#     finally:
#         try:
#             cur.close()
#         except Exception:
#             pass


# ----------------------------- UNASSIGNMENT -----------------------------
@api_routes.route("/api/v1/unassign/<int:exam_id>", methods=["POST"])
def unassign_course(exam_id):
    """
    Unassign all students of this exam from all legans.
    Steps:
      1) Check exam exists
      2) Get all student_legan rows for this exam
      3) Log each to student_legan_history (UNASSIGNED)
      4) Delete from student_legan
      5) Mark exam.assigned = 0
    """
    cur = None
    try:
        cur = db.cursor(dictionary=True)

        # 1) check exam
        cur.execute("SELECT * FROM exam WHERE Exam_id=%s", (exam_id,))
        exam = cur.fetchone()
        if not exam:
            return jsonify({"error": "Exam not found"}), 404

        # 2) get all assigned students for this exam
        cur.execute("""
            SELECT legan_id, student_id
            FROM student_legan
            WHERE Exam = %s
        """, (exam_id,))
        rows = cur.fetchall()
        if not rows:
            return jsonify({"message": "No students assigned for this exam"}), 200

        # 3) write UNASSIGNED history
        hist = db.cursor()
        for r in rows:
            hist.execute("""
                INSERT INTO student_legan_history (legan_id, student_id, exam_id, action)
                VALUES (%s, %s, %s, 'UNASSIGNED')
            """, (r["legan_id"], r["student_id"], exam_id))

        # 4) delete from student_legan
        delc = db.cursor()
        delc.execute("""
            DELETE FROM student_legan
            WHERE Exam = %s
        """, (exam_id,))

        # 5) mark exam.assigned = 0
        cur2 = db.cursor()
        cur2.execute("UPDATE exam SET assigned=0 WHERE Exam_id=%s", (exam_id,))

        db.commit()
        hist.close()
        delc.close()
        cur2.close()

        return jsonify({
            "message": f"✅ Unassigned {len(rows)} students from exam {exam_id}.",
            "unassigned": len(rows)
        }), 200

    except Exception as e:
        if db.is_connected() and cur:
            db.rollback()
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            cur.close()
        except Exception:
            pass


# ----------------------------- PRINT JSON -----------------------------
@api_routes.route("/api/v1/students-legans/print", methods=["GET"])
def print_students_legans_json():
    """
    Return JSON grouped by *exam_id + legan_id*:
    [
      {
        legan_id, legan_name, room_name, floor,
        program_arabic_name, program_english_name, program_logo,
        level, course, day, period_id, date,
        students: [...]
      }
    ]
    """
    cur = None
    try:
        where, params = _build_exam_filters("e")
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""

        sql = f"""
            SELECT
              e.Exam_id, e.year,e.program AS program_id, e.level AS exam_level, e.code_course AS course,
              e.day, e.period_id, e.date, e.type,
              l.legan_id, l.legan_name, l.capacity,
              r.room_name, r.floor AS floor,
              p.logo AS program_logo,
              p.arabic_name AS program_arabic_name,
              p.English_name AS program_english_name,
              sl.student_legan_id, s.student_ID, s.student_name,s.payment,s.level
            FROM exam e
            LEFT JOIN student_legan sl ON sl.Exam=e.Exam_id
            LEFT JOIN legan l ON l.legan_id=sl.legan_id
            LEFT JOIN rooms r ON r.room_id=l.room_id
            LEFT JOIN programs p ON p.program_id=e.program
            LEFT JOIN registration s ON s.student_ID=sl.student_id
            {where_sql}
            ORDER BY
              {DAY_ORDER_SQL}, {PERIOD_MINUTES_SQL}, e.Exam_id,
              l.legan_id, s.student_ID
        """
        cur = db.cursor(dictionary=True)
        cur.execute(sql, params)
        rows = cur.fetchall()

        # ----- FIX: group by exam_id + legan_id (NOT just legan_id) -----
        bucket = {}
        for row in rows:
            if not row["legan_id"]:
                # skip stray records with no legan
                continue

            key = f"{row['Exam_id']}_{row['legan_id']}"  # ✅ unique per exam+legan

            if key not in bucket:
                bucket[key] = {
                    "exam_id": row["Exam_id"],              # ✅ include exam id
                    "legan_id": row["legan_id"],
                    "legan_name": row["legan_name"],
                    "room_name": row["room_name"],
                    "floor": row["floor"],
                    "capacity": row["capacity"],
                    "level": row["exam_level"],
                    "course": row["course"],
                    "day": row["day"],
                    "period_id": row["period_id"],
                    "date": row["date"],
                    "students": []
                }

            # ✅ add student ONLY if not already in the list
            if row["student_ID"]:
                students = bucket[key]["students"]
                if not any(s["student_legan_id"] == row["student_legan_id"] for s in students):
                    students.append({
                        "student_legan_id": row["student_legan_id"],
                        "student_id": row["student_ID"],
                        "student_name": row["student_name"],
                        "payment": row["payment"],
                        "level": row["level"],
                    })

        # convert to list
        legans = list(bucket.values())

        return jsonify({
            "legans": legans,
            "exam_info": { "type": (rows[0]["type"] if rows else None), "program": (rows[0]["program_id"] if rows else None),"year": (rows[0]["year"] if rows else None) }
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        try: cur.close()
        except Exception: pass

# -------------------------------------all day legans ---------------------------------
@api_routes.route("/api/v1/students-legans/print/day", methods=["GET"])
def print_students_legans_by_day():
    """
    Print all legans for a specific day across all programs,
    ordered by Program → Level → Period.
    Example: /api/v1/students-legans/print/day?day=saturday
    """
    cur = None
    try:
        day = request.args.get("day")
        if not day:
            return jsonify({"error": "Missing day parameter"}), 400

        # ✅ Ensure MySQL connection is alive before using it
        if not db.is_connected():
            try:
                db.reconnect(attempts=3, delay=2)
                print("🔄 MySQL reconnected successfully")
            except Exception as e:
                print("❌ MySQL reconnect failed:", e)
                return jsonify({"error": "Database reconnection failed"}), 500

        cur = db.cursor(dictionary=True)

        sql = f"""
            SELECT
              e.Exam_id, e.year, e.program AS program_id, e.level AS exam_level,
              e.code_course AS course, e.day, e.period_id, e.date, e.type,
              l.legan_id, l.legan_name, l.capacity,
              r.room_name, r.floor AS floor,
              p.logo AS program_logo,
              p.arabic_name AS program_arabic_name,
              p.English_name AS program_english_name,
              sl.student_legan_id, s.student_ID, s.student_name, s.payment, s.level
            FROM exam e
            LEFT JOIN student_legan sl ON sl.Exam=e.Exam_id
            LEFT JOIN legan l ON l.legan_id=sl.legan_id
            LEFT JOIN rooms r ON r.room_id=l.room_id
            LEFT JOIN programs p ON p.program_id=e.program
            LEFT JOIN registration s ON s.student_ID=sl.student_id
            WHERE LOWER(e.day) = LOWER(%s)
            ORDER BY
              FIELD(e.program, 'BIDT', 'BIAS', 'DEE', 'LSC'),
              CAST(SUBSTRING(e.level, 7) AS UNSIGNED),  -- handles "level 1", "level 2", etc.
              {PERIOD_MINUTES_SQL},
              l.legan_id, s.student_ID
        """

        cur.execute(sql, (day,))
        rows = cur.fetchall()

        # -------- Group data --------
        bucket = {}
        for row in rows:
            if not row["legan_id"]:
                continue

            key = f"{row['program_id']}_{row['exam_level']}_{row['period_id']}_{row['legan_id']}"

            if key not in bucket:
                bucket[key] = {
                    "program": row["program_id"],
                    "program_arabic_name": row["program_arabic_name"],
                    "program_english_name": row["program_english_name"],
                    "program_logo": row["program_logo"],
                    "level": row["exam_level"],
                    "course": row["course"],
                    "day": row["day"],
                    "period_id": row["period_id"],
                    "date": row["date"],
                    "legan_id": row["legan_id"],
                    "legan_name": row["legan_name"],
                    "room_name": row["room_name"],
                    "floor": row["floor"],
                    "capacity": row["capacity"],
                    "students": []
                }

            if row["student_ID"]:
                students = bucket[key]["students"]
                if not any(s["student_legan_id"] == row["student_legan_id"] for s in students):
                    students.append({
                        "student_legan_id": row["student_legan_id"],
                        "student_id": row["student_ID"],
                        "student_name": row["student_name"],
                        "payment": row["payment"],
                        "level": row["level"],
                    })

        # Convert to structured data: Program → Level → Period → Legans
        programs = {}
        for legan in bucket.values():
            prog = legan["program"]
            lvl = legan["level"]
            prd = legan["period_id"]

            programs.setdefault(prog, {})
            programs[prog].setdefault(lvl, {})
            programs[prog][lvl].setdefault(prd, [])
            programs[prog][lvl][prd].append(legan)

        return jsonify({
            "day": day,
            "year": (rows[0]["year"] if rows else None),
            "programs": programs
        }), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            if cur:
                cur.close()
        except Exception:
            pass

        
        
    # ----------------------------- PRINT PDF -----------------------------


@api_routes.route("/api/v1/students-legans/print/pdf", methods=["GET"])
def print_students_legans_pdf():
    try:
        # ✅ نفس منطق JSON route: ضمان exam_id
        exam_id = request.args.get("exam_id")
        if not exam_id:
            return jsonify({"error": "exam_id is required"}), 400

        # --- إعادة استخدام نفس SQL و grouping ---
        where, params = _build_exam_filters("e")
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""

        sql = f"""
            SELECT
              e.Exam_id, e.year,e.program AS program_id, e.level, e.code_course AS course,
              e.day, e.period_id, e.date, e.type,
              l.legan_id, l.legan_name, l.capacity,
              r.room_name, r.floor AS floor,
              p.logo AS program_logo,
              p.arabic_name AS program_arabic_name,
              p.English_name AS program_english_name,
              sl.student_legan_id, s.student_ID, s.student_name,s.payment
            FROM exam e
            LEFT JOIN student_legan sl ON sl.Exam=e.Exam_id
            LEFT JOIN legan l ON l.legan_id=sl.legan_id
            LEFT JOIN rooms r ON r.room_id=l.room_id
            LEFT JOIN programs p ON p.program_id=e.program
            LEFT JOIN registration s ON s.student_ID=sl.student_id
            {where_sql}
            ORDER BY
              {DAY_ORDER_SQL}, {PERIOD_MINUTES_SQL}, e.Exam_id,
              l.legan_id, s.student_ID
        """
        cur = db.cursor(dictionary=True)
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()

        # ---- Group لجان ----
        bucket = {}
        for row in rows:
            if not row["legan_id"]:
                continue
            key = f"{row['Exam_id']}_{row['legan_id']}"
            if key not in bucket:
                bucket[key] = {
                    "legan_id": row["legan_id"],
                    "legan_name": row["legan_name"],
                    "room_name": row["room_name"],
                    "floor": row["floor"],
                    "capacity": row["capacity"],
                    "students": []
                }
            if row["student_ID"]:
                bucket[key]["students"].append({
                    "id": row["student_ID"],
                    "name": row["student_name"]
                })

        # ---- بناء PDF ----
        pdf_buffer = BytesIO()
        p = canvas.Canvas(pdf_buffer, pagesize=A4)
        width, height = A4

        for legan_key, data in bucket.items():
            p.setFont("Helvetica-Bold", 16)
            p.drawString(50, height - 50, f"لجنة: {data['legan_name']}")
            p.setFont("Helvetica", 12)
            p.drawString(50, height - 80, f"قاعة: {data['room_name']}  |  الدور: {data['floor']}")
            p.drawString(50, height - 100, f"السعة: {data['capacity']}")

            y = height - 150
            p.setFont("Helvetica", 11)

            for s in data["students"]:
                p.drawString(60, y, f"{s['id']} - {s['name']}")
                y -= 20
                if y < 100:
                    p.showPage()
                    y = height - 100

            p.showPage()

        p.save()
        pdf_buffer.seek(0)

        return send_file(pdf_buffer, as_attachment=True,
                         download_name=f"exam_{exam_id}_legans.pdf",
                         mimetype="application/pdf")

    except Exception as e:
        print(str(e))
        return jsonify({"error": str(e)}), 500


# from flask import Blueprint, send_file, request, jsonify
# import pandas as pd
# from io import BytesIO
# import os
# import mysql.connector
# from werkzeug.utils import secure_filename
# import traceback 

# # Initialize DB connection (assuming 'HNU-exams' and credentials are correct)
# db = mysql.connector.connect(
#     host="localhost",
#     user="root",        # Default for XAMPP
#     password="",        # Leave empty unless you have one
#     database="HNU-exam"  # Your DB name
# )

# UPLOAD_FOLDER = "uploads"
# os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# api_routes = Blueprint("api_routes", __name__)

# @api_routes.route("/api/hello")
# def hello():
#     return jsonify({"message": "Hello from Flask backend!"})

# # ======================================================================
# # DOCTORS ROUTES
# # ======================================================================

# @api_routes.route("/api/v1/doctors/upload", methods=["GET"])
# def download_doctor_template():
#     # Create a simple Excel template in memory
#     columns = [ "doctor_name", "email", "phone"]
#     df = pd.DataFrame(columns=columns)

#     output = BytesIO()
#     with pd.ExcelWriter(output, engine='openpyxl') as writer:
#         df.to_excel(writer, index=False, sheet_name="DoctorsTemplate")
#     output.seek(0)

#     return send_file(
#         output,
#         as_attachment=True,
#         download_name="doctor_template.xlsx",
#         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
#     )

# @api_routes.route("/api/v1/doctors/upload", methods=["POST"])
# def upload_doctor_file():
#     if "file" not in request.files:
#         return jsonify({"error": "No file part"}), 400

#     file = request.files["file"]
#     if file.filename == "":
#         return jsonify({"error": "No file selected"}), 400

#     filename = secure_filename(file.filename)
#     filepath = os.path.join(UPLOAD_FOLDER, filename)
    
#     cursor = None
    
#     try:
#         file.save(filepath)
#         df = pd.read_excel(filepath)
        
#         required_columns = { "doctor_name", "email", "phone"}
#         if not required_columns.issubset(df.columns):
#             missing = required_columns - set(df.columns)
#             return jsonify({"error": f"Invalid Excel format. Missing columns: {', '.join(missing)}"}), 400

#         cursor = db.cursor()
#         records_to_insert = []
        
#         for _, row in df.iterrows():
#             name = str(row.get("doctor_name", "")).strip()
#             email = str(row.get("email", "")).strip()
#             phone = str(row.get("phone", "")).strip()
            
#             if not name or not email:
#                 print(f"Skipping doctor row due to missing required data: {row.to_dict()}")
#                 continue
                
#             records_to_insert.append((name, email, phone))

#         sql = "INSERT INTO doctors (doctor_name, email, phone) VALUES (%s, %s, %s)"
        
#         if records_to_insert:
#             cursor.executemany(sql, records_to_insert)
#             db.commit()
#             count = len(records_to_insert)
            
#             return jsonify({"message": f"Successfully uploaded {count} doctor(s)!"}), 201
#         else:
#             return jsonify({"message": "No valid doctor records found to upload."}), 200

#     except Exception as e:
#         if db.is_connected() and cursor:
#             db.rollback()
        
#         traceback.print_exc() 
#         print("❌ Doctor Upload Error:", e)
        
#         return jsonify({"error": f"An error occurred during doctor processing: {str(e)}"}), 500

#     finally:
#         if cursor:
#             cursor.close()
        
#         if os.path.exists(filepath):
#             os.remove(filepath)
            
# # ======================================================================
# # ROOMS ROUTES
# # ======================================================================

# @api_routes.route("/api/v1/rooms/upload", methods=["GET"])
# def download_room_template():
#     """Provides an Excel template for room data upload."""
#     # Define the columns for the Room template
#     columns = [ "room_name", "capacity", "floor"]
#     df = pd.DataFrame(columns=columns)

#     output = BytesIO()
#     with pd.ExcelWriter(output, engine='openpyxl') as writer:
#         df.to_excel(writer, index=False, sheet_name="RoomsTemplate")
#     output.seek(0)

#     return send_file(
#         output,
#         as_attachment=True,
#         download_name="room_template.xlsx",
#         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
#     )

# @api_routes.route("/api/v1/rooms/upload", methods=["POST"])
# def upload_room_file():
#     """Handles the upload and database insertion of room data from an Excel file."""
#     if "file" not in request.files:
#         return jsonify({"error": "No file part"}), 400

#     file = request.files["file"]
#     if file.filename == "":
#         return jsonify({"error": "No file selected"}), 400

#     filename = secure_filename(file.filename)
#     filepath = os.path.join(UPLOAD_FOLDER, filename)
    
#     cursor = None
    
#     try:
#         file.save(filepath)
#         df = pd.read_excel(filepath)
        
#         # Validation: Check for required columns
#         required_columns = { "room_name", "capacity", "floor"}
#         if not required_columns.issubset(df.columns):
#             missing = required_columns - set(df.columns)
#             return jsonify({"error": f"Invalid Excel format. Missing columns: {', '.join(missing)}"}), 400

#         cursor = db.cursor()
#         records_to_insert = []
        
#         for _, row in df.iterrows():
#             # Data extraction and cleaning/type casting
#             room_name = str(row.get("room_name", "")).strip()
#             floor = str(row.get("floor", "")).strip()

            
#             # Convert capacity and floor to integer, handling potential NaN or missing values
#             try:
#                 capacity = int(row.get("capacity", 0))
#             except (ValueError, TypeError):
#                 print(f"Skipping room row due to invalid numeric data: {row.to_dict()}")
#                 continue
            
#             if not room_name or capacity <= 0:
#                 print(f"Skipping room row due to missing required data: {row.to_dict()}")
#                 continue
                
#             records_to_insert.append((room_name, capacity, floor))

#         sql = "INSERT INTO rooms (room_name, capacity, floor) VALUES (%s, %s, %s)"
        
#         if records_to_insert:
#             cursor.executemany(sql, records_to_insert)
#             db.commit()
#             count = len(records_to_insert)
            
#             return jsonify({"message": f"Successfully uploaded {count} room(s)!"}), 201
#         else:
#             return jsonify({"message": "No valid room records found to upload."}), 200

#     except Exception as e:
#         if db.is_connected() and cursor:
#             db.rollback()
        
#         traceback.print_exc() 
#         print("❌ Room Upload Error:", e)
        
#         return jsonify({"error": f"An error occurred during room processing: {str(e)}"}), 500

#     finally:
#         if cursor:
#             cursor.close()
        
#         if os.path.exists(filepath):
#             os.remove(filepath)

# # ======================================================================
# # COURSES ROUTES
# # ======================================================================

# @api_routes.route("/api/v1/courses/upload", methods=["GET"])
# def download_course_template():
#     """Provides an Excel template for course data upload."""
#     # Columns requested by the user
#     columns = ["Course_code", "course_name", "course_arab_name", "credit_hrs", "prerequisite", "type", "lab"]
#     df = pd.DataFrame(columns=columns)

#     output = BytesIO()
#     with pd.ExcelWriter(output, engine='openpyxl') as writer:
#         df.to_excel(writer, index=False, sheet_name="CoursesTemplate")
#     output.seek(0)

#     return send_file(
#         output,
#         as_attachment=True,
#         download_name="course_template.xlsx",
#         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
#     )

# @api_routes.route("/api/v1/courses/upload", methods=["POST"])
# def upload_course_file():
#     """Handles the upload and database insertion of course data from an Excel file."""
#     if "file" not in request.files:
#         return jsonify({"error": "No file part"}), 400

#     file = request.files["file"]
#     if file.filename == "":
#         return jsonify({"error": "No file selected"}), 400

#     filename = secure_filename(file.filename)
#     filepath = os.path.join(UPLOAD_FOLDER, filename)
    
#     cursor = None
    
#     try:
#         file.save(filepath)
#         df = pd.read_excel(filepath)
        
#         # Validation: Check for required columns
#         required_columns = {"Course_code", "course_name", "course_arab_name", "credit_hrs", "type"}
#         if not required_columns.issubset(df.columns):
#             missing = required_columns - set(df.columns)
#             return jsonify({"error": f"Invalid Excel format. Missing required columns: {', '.join(missing)}"}), 400

#         cursor = db.cursor()
#         records_to_insert = []
        
#         for _, row in df.iterrows():
#             course_code = str(row.get("Course_code", "")).strip()
#             course_name = str(row.get("course_name", "")).strip()
#             course_arab_name = str(row.get("course_arab_name", "")).strip()
#             prerequisite = str(row.get("prerequisite", "")).strip()
#             course_type = str(row.get("type", "")).strip()
#             lab = str(row.get("lab", "")).strip()
            
#             # Type Casting and Validation for credit_hrs
#             try:
#                 # Convert to integer, defaulting to 0 if missing or invalid
#                 credit_hrs = int(row.get("credit_hrs", 0))
#             except (ValueError, TypeError):
#                 print(f"Skipping course row due to invalid credit_hrs: {row.to_dict()}")
#                 continue
            
#             # Simple check for required data
#             if not course_code or not course_name or credit_hrs <= 0 or not course_type:
#                 print(f"Skipping course row due to missing required data: {row.to_dict()}")
#                 continue
                
#             records_to_insert.append((
#                 course_code, 
#                 course_name, 
#                 course_arab_name, 
#                 credit_hrs, 
#                 prerequisite, 
#                 course_type, 
#                 lab
#             ))

#         sql = """
#         INSERT INTO courses (
#             Course_code, course_name, course_arab_name, credit_hrs, 
#             prerequisite, type, lab
#         ) 
#         VALUES (%s, %s, %s, %s, %s, %s, %s)
#         """
        
#         if records_to_insert:
#             cursor.executemany(sql, records_to_insert)
#             db.commit()
#             count = len(records_to_insert)
            
#             return jsonify({"message": f"Successfully uploaded {count} course(s)!"}), 201
#         else:
#             return jsonify({"message": "No valid course records found to upload."}), 200

#     except Exception as e:
#         if db.is_connected() and cursor:
#             db.rollback()
        
#         traceback.print_exc() 
#         print("❌ Course Upload Error:", e)
        
#         return jsonify({"error": f"An error occurred during course processing: {str(e)}"}), 500

#     finally:
#         if cursor:
#             cursor.close()
        
#         if os.path.exists(filepath):
#             os.remove(filepath)

# # ======================================================================
# # STUDENTS ROUTES
# # ======================================================================

# @api_routes.route("/api/v1/students/upload", methods=["GET"])
# def download_student_template():
#     """Provides an Excel template for student data upload."""
#     columns = [
#         "student_ID", "NID", "Arab_name", "Eng_name", "HNU_email", 
#         "phone_number", "parent_number", "address", "medical_status"
#     ]
#     df = pd.DataFrame(columns=columns)

#     output = BytesIO()
#     with pd.ExcelWriter(output, engine='openpyxl') as writer:
#         df.to_excel(writer, index=False, sheet_name="StudentsTemplate")
#     output.seek(0)

#     return send_file(
#         output,
#         as_attachment=True,
#         download_name="student_template.xlsx",
#         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
#     )

# @api_routes.route("/api/v1/students/upload", methods=["POST"])
# def upload_student_file():
#     """Handles the upload and database insertion of student data from an Excel file."""
#     if "file" not in request.files:
#         return jsonify({"error": "No file part"}), 400

#     file = request.files["file"]
#     if file.filename == "":
#         return jsonify({"error": "No file selected"}), 400

#     filename = secure_filename(file.filename)
#     filepath = os.path.join(UPLOAD_FOLDER, filename)
    
#     cursor = None
    
#     try:
#         file.save(filepath)
#         df = pd.read_excel(filepath)
        
#         # Validation: Check for required columns
#         required_columns = {"student_ID", "NID", "Arab_name", "Eng_name", "HNU_email"}
#         if not required_columns.issubset(df.columns):
#             missing = required_columns - set(df.columns)
#             return jsonify({"error": f"Invalid Excel format. Missing required columns: {', '.join(missing)}"}), 400

#         cursor = db.cursor()
#         records_to_insert = []
        
#         for _, row in df.iterrows():
#             # Data extraction and cleaning (all cast to string for simplicity)
#             student_id = str(row.get("student_ID", "")).strip()
#             nid = str(row.get("NID", "")).strip()
#             arab_name = str(row.get("Arab_name", "")).strip()
#             eng_name = str(row.get("Eng_name", "")).strip()
#             hnu_email = str(row.get("HNU_email", "")).strip()
#             phone_number = str(row.get("phone_number", "")).strip()
#             parent_number = str(row.get("parent_number", "")).strip()
#             address = str(row.get("address", "")).strip()
#             medical_status = str(row.get("medical_status", "")).strip()
            
#             # Simple check for required data (IDs and names/email)
#             if not student_id or not nid or not arab_name or not eng_name or not hnu_email:
#                 print(f"Skipping student row due to missing required data: {row.to_dict()}")
#                 continue
                
#             records_to_insert.append((
#                 student_id, nid, arab_name, eng_name, hnu_email, 
#                 phone_number, parent_number, address, medical_status
#             ))

#         sql = """
#         INSERT INTO students (
#             student_ID, NID, Arab_name, Eng_name, HNU_email, 
#             phone_number, parent_number, address, medical_status
#         ) 
#         VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
#         """
        
#         if records_to_insert:
#             cursor.executemany(sql, records_to_insert)
#             db.commit()
#             count = len(records_to_insert)
            
#             return jsonify({"message": f"Successfully uploaded {count} student(s)!"}), 201
#         else:
#             return jsonify({"message": "No valid student records found to upload."}), 200

#     except Exception as e:
#         if db.is_connected() and cursor:
#             db.rollback()
        
#         traceback.print_exc() 
#         print("❌ Student Upload Error:", e)
        
#         return jsonify({"error": f"An error occurred during student processing: {str(e)}"}), 500

#     finally:
#         if cursor:
#             cursor.close()
        
#         if os.path.exists(filepath):
#             os.remove(filepath)



# # ======================================================================
# # REGISTRATION ROUTES (UPDATED)
# # ======================================================================
# @api_routes.route("/api/v1/Student-registration", methods=["GET"])
# def get_all_registrations():
#     """Fetch all registration records."""
#     try:
#         cursor = db.cursor(dictionary=True)
#         cursor.execute("""
#             SELECT 
#                 NID, level, student_ID, course, 
#                 student_group, student_name, payment, program
#             FROM registration
#         """)
#         rows = cursor.fetchall()
#         return jsonify(rows), 200
#     except Exception as e:
#         traceback.print_exc()
#         return jsonify({"error": str(e)}), 500
#     finally:
#         cursor.close()


# @api_routes.route("/api/v1/Student-registration", methods=["POST"])
# def add_registration():
#     """Add a single student registration record."""
#     try:
#         data = request.get_json()

#         required_fields = ["NID", "level", "student_ID", "course", "student_name"]
#         missing = [f for f in required_fields if f not in data or not data[f]]

#         if missing:
#             return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

#         sql = """
#         INSERT INTO registration (
#             NID, level, student_ID, course, student_group, student_name, payment, program
#         ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
#         """
#         values = (
#             data.get("NID"),
#             data.get("level"),
#             data.get("student_ID"),
#             data.get("course"),
#             data.get("student_group"),
#             data.get("student_name"),
#             data.get("payment"),
#             data.get("program")
#         )

#         cursor = db.cursor()
#         cursor.execute(sql, values)
#         db.commit()

#         return jsonify({"message": "✅ Registration record added successfully!"}), 201
#     except Exception as e:
#         traceback.print_exc()
#         return jsonify({"error": str(e)}), 500
#     finally:
#         cursor.close()


# @api_routes.route("/api/v1/Student-registration/<student_ID>", methods=["PUT"])
# def update_registration(student_ID):
#     """Update a registration record by student_ID."""
#     try:
#         data = request.get_json()

#         fields = ["NID", "level", "course", "student_group", "student_name", "payment", "program"]
#         updates = []
#         values = []

#         for field in fields:
#             if field in data:
#                 updates.append(f"{field} = %s")
#                 values.append(data[field])

#         if not updates:
#             return jsonify({"error": "No fields provided for update."}), 400

#         sql = f"UPDATE registration SET {', '.join(updates)} WHERE student_ID = %s"
#         values.append(student_ID)

#         cursor = db.cursor()
#         cursor.execute(sql, values)
#         db.commit()

#         if cursor.rowcount == 0:
#             return jsonify({"message": "No registration found with that student_ID."}), 404

#         return jsonify({"message": "✅ Registration updated successfully!"}), 200
#     except Exception as e:
#         traceback.print_exc()
#         return jsonify({"error": str(e)}), 500
#     finally:
#         cursor.close()


# @api_routes.route("/api/v1/Student-registration/<student_ID>", methods=["DELETE"])
# def delete_registration(student_ID):
#     """Delete a registration record by student_ID."""
#     try:
#         cursor = db.cursor()
#         cursor.execute("DELETE FROM registration WHERE student_ID = %s", (student_ID,))
#         db.commit()

#         if cursor.rowcount == 0:
#             return jsonify({"message": "No registration found with that student_ID."}), 404

#         return jsonify({"message": "🗑️ Registration record deleted successfully!"}), 200
#     except Exception as e:
#         traceback.print_exc()
#         return jsonify({"error": str(e)}), 500
#     finally:
#         cursor.close()


# @api_routes.route("/api/v1/Student-registration/upload", methods=["GET"])
# def download_registration_template():
#     """Provides an Excel template for student registration data upload."""
#     columns = [
#         "NID", 
#         "level", 
#         "student_ID", 
#         "course", 
#         "student_group", 
#         "student_name", 
#         "payment", 
#         "program"
#     ]
#     df = pd.DataFrame(columns=columns)

#     output = BytesIO()
#     with pd.ExcelWriter(output, engine='openpyxl') as writer:
#         df.to_excel(writer, index=False, sheet_name="RegistrationTemplate")
#     output.seek(0)

#     return send_file(
#         output,
#         as_attachment=True,
#         download_name="registration_template.xlsx",
#         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
#     )


# @api_routes.route("/api/v1/Student-registration/upload", methods=["POST"])
# def upload_registration_file():
#     """Handles the upload and database insertion of updated student registration data."""
#     if "file" not in request.files:
#         return jsonify({"error": "No file part"}), 400

#     file = request.files["file"]
#     if file.filename == "":
#         return jsonify({"error": "No file selected"}), 400

#     filename = secure_filename(file.filename)
#     filepath = os.path.join(UPLOAD_FOLDER, filename)

#     cursor = None

#     try:
#         file.save(filepath)
#         df = pd.read_excel(filepath)

#         # Validate required columns
#         required_columns = {"NID", "level", "student_ID", "course", "student_name"}
#         if not required_columns.issubset(df.columns):
#             missing = required_columns - set(df.columns)
#             return jsonify({
#                 "error": f"Invalid Excel format. Missing required columns: {', '.join(missing)}"
#             }), 400

#         cursor = db.cursor()
#         records_to_insert = []

#         for _, row in df.iterrows():
#             # Extract and clean each value
#             NID = str(row.get("NID", "")).strip()
#             level = str(row.get("level", "")).strip()
#             student_ID = str(row.get("student_ID", "")).strip()
#             course = str(row.get("course", "")).strip()
#             student_group = str(row.get("student_group", "")).strip()
#             student_name = str(row.get("student_name", "")).strip()
#             payment = str(row.get("payment", "")).strip()
#             notes = str(row.get("notes", "")).strip()

#             # Skip invalid rows
#             if not NID or not level or not student_ID or not course or not student_name:
#                 print(f"Skipping registration row due to missing data: {row.to_dict()}")
#                 continue

#             records_to_insert.append((
#                 NID, level, student_ID, course, student_group, student_name, payment, notes
#             ))

#         # Insert SQL — make sure your DB table has these columns
#         sql = """
#         INSERT INTO registration (
#             NID, level, student_ID, course, student_group, student_name, payment, notes
#         ) 
#         VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
#         """

#         if records_to_insert:
#             cursor.executemany(sql, records_to_insert)
#             db.commit()
#             count = len(records_to_insert)
#             return jsonify({"message": f"✅ Successfully uploaded {count} registration record(s)!"}), 201
#         else:
#             return jsonify({"message": "No valid registration records found to upload."}), 200

#     except Exception as e:
#         if db.is_connected() and cursor:
#             db.rollback()

#         traceback.print_exc()
#         print("❌ Registration Upload Error:", e)
#         return jsonify({"error": f"An error occurred during registration processing: {str(e)}"}), 500

#     finally:
#         if cursor:
#             cursor.close()
#         if os.path.exists(filepath):
#             os.remove(filepath)

# # ======================================================================
# # NEW EXAMS ROUTES
# # ======================================================================

# @api_routes.route("/api/v1/exams/upload", methods=["GET"])
# def download_exam_template():
#     """Provides an Excel template for exam data upload (includes 'level')."""
#     columns = [
#         "Exam_id", "year", "semester", "type", "period_id", "date", 
#         "program_code", "course_code", "day", "level"
#     ]
#     df = pd.DataFrame(columns=columns)

#     output = BytesIO()
#     with pd.ExcelWriter(output, engine='openpyxl') as writer:
#         df.to_excel(writer, index=False, sheet_name="ExamsTemplate")
#     output.seek(0)

#     return send_file(
#         output,
#         as_attachment=True,
#         download_name="exam_template.xlsx",
#         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
#     )


# @api_routes.route("/api/v1/exams/upload", methods=["POST"])
# def upload_exam_file():
#     """Handles the upload and database insertion of exam data from an Excel file (with 'level')."""
#     if "file" not in request.files:
#         return jsonify({"error": "No file part"}), 400

#     file = request.files["file"]
#     if file.filename == "":
#         return jsonify({"error": "No file selected"}), 400

#     filename = secure_filename(file.filename)
#     filepath = os.path.join(UPLOAD_FOLDER, filename)
    
#     cursor = None
    
#     try:
#         file.save(filepath)
#         df = pd.read_excel(filepath)
        
#         # Validation: Check for required columns
#         required_columns = {"Exam_id", "year", "semester", "course_code", "date", "level"}
#         if not required_columns.issubset(df.columns):
#             missing = required_columns - set(df.columns)
#             return jsonify({"error": f"Invalid Excel format. Missing required columns: {', '.join(missing)}"}), 400

#         cursor = db.cursor()
#         records_to_insert = []
        
#         for _, row in df.iterrows():
#             exam_id = str(row.get("Exam_id", "")).strip()
#             year = str(row.get("year", "")).strip()
#             semester = str(row.get("semester", "")).strip()
#             exam_type = str(row.get("type", "")).strip()
#             period_id = str(row.get("period_id", "")).strip()
#             exam_date = str(row.get("date", "")).strip()
#             program_code = str(row.get("program_code", "")).strip()
#             course_code = str(row.get("course_code", "")).strip()
#             day = str(row.get("day", "")).strip()
#             level = str(row.get("level", "")).strip()
            
#             # Validate year
          
            
#             # Skip incomplete rows
#             if not exam_id or not semester or not course_code or not exam_date or len(year) <= 0:
#                 print(f"Skipping exam row due to missing required data: {row.to_dict()}")
#                 continue
                
#             records_to_insert.append((
#                 exam_id, year, semester, exam_type, period_id, 
#                 exam_date, program_code, course_code, day, level
#             ))

#         sql = """
#         INSERT INTO exam (
#             Exam_id, year, semester, type, period_id, date, 
#             program, code_course, day, level
#         ) 
#         VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
#         """
        
#         if records_to_insert:
#             cursor.executemany(sql, records_to_insert)
#             db.commit()
#             count = len(records_to_insert)
            
#             return jsonify({"message": f"✅ Successfully uploaded {count} exam record(s)!"}), 201
#         else:
#             return jsonify({"message": "⚠️ No valid exam records found to upload."}), 200

#     except Exception as e:
#         if db.is_connected() and cursor:
#             db.rollback()
        
#         traceback.print_exc() 
#         print("❌ Exam Upload Error:", e)
        
#         return jsonify({"error": f"An error occurred during exam processing: {str(e)}"}), 500

#     finally:
#         if cursor:
#             cursor.close()
        
#         if os.path.exists(filepath):
#             os.remove(filepath)


# # ----------------------------------------------------------------------
# # LEGAN ROUTES (Corrected)
# # ----------------------------------------------------------------------
# @api_routes.route("/api/v1/legans/upload", methods=["GET"])
# def download_legan_template(): # <-- Renamed function
#     """Provides an Excel template for Legan (Exam Committee/Control) data upload."""
#     columns = [
#         "Legan_id", "legan_name", "room_id", "level", "capacity", "full_capacity", 
#         "program"
#     ]
#     df = pd.DataFrame(columns=columns)

#     output = BytesIO()
#     with pd.ExcelWriter(output, engine='openpyxl') as writer:
#         df.to_excel(writer, index=False, sheet_name="LegansTemplate") # <-- Renamed sheet
#     output.seek(0)

#     return send_file(
#         output,
#         as_attachment=True,
#         download_name="legan_template.xlsx", # <-- Renamed file
#         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
#     )

# @api_routes.route("/api/v1/legans/upload", methods=["POST"])
# def upload_legan_file(): # <-- Renamed function
#     """Handles the upload and database insertion of Legan data from an Excel file."""
#     if "file" not in request.files:
#         return jsonify({"error": "No file part"}), 400

#     file = request.files["file"]
#     if file.filename == "":
#         return jsonify({"error": "No file selected"}), 400

#     filename = secure_filename(file.filename)
#     filepath = os.path.join(UPLOAD_FOLDER, filename)
    
#     cursor = None
    
#     try:
#         file.save(filepath)
#         df = pd.read_excel(filepath)
        
#         # Validation: Check for required columns
#         required_columns = {"Legan_id", "legan_name", "room_id", "level", "capacity", "full_capacity", "program"}
#         if not required_columns.issubset(df.columns):
#             missing = required_columns - set(df.columns)
#             return jsonify({"error": f"Invalid Excel format. Missing required columns: {', '.join(missing)}"}), 400

#         cursor = db.cursor()
#         records_to_insert = []
        
#         for _, row in df.iterrows():
#             # Data extraction and cleaning
#             Legan_id = str(row.get("Legan_id", "")).strip()
#             legan_name = str(row.get("legan_name", "")).strip()
#             room_id_str = str(row.get("room_id", "")).strip()
#             level_str = str(row.get("level", "")).strip()
#             capacity_str = str(row.get("capacity", "")).strip()
#             full_capacity_str = str(row.get("full_capacity", "")).strip()
#             program = str(row.get("program", "")).strip()
       
#             # Type Casting and Validation for numeric fields
#             try:
#                 # Assuming these should be integers
#                 room_id = int(float(room_id_str)) if room_id_str else 0
#                 level = (level_str) if level_str else 'level 1'
#                 capacity = int(float(capacity_str)) if capacity_str else 0
#                 full_capacity = int(float(full_capacity_str)) if full_capacity_str else 0
#             except (ValueError, TypeError):
#                 print(f"Skipping legan row due to invalid numeric data: {row.to_dict()}")
#                 continue
            
#             # Simple check for required data (Legan_id, name, program, and at least one capacity field > 0)
#             if not Legan_id or not legan_name or not program or (capacity <= 0 and full_capacity <= 0):
#                 print(f"Skipping legan row due to missing required data: {row.to_dict()}")
#                 continue
                
#             records_to_insert.append((
#                 Legan_id, legan_name, room_id, level, capacity, 
#                 full_capacity, program
#             ))

#         # *** CRITICAL FIX: Corrected table name from 'exam' to 'legans' 
#         # (Assuming the table is named 'legans' and columns match the provided list)
#         sql = """
#         INSERT INTO legan (
#             Legan_id, legan_name, room_id, level, capacity, 
#             full_capacity, program
#         ) 
#         VALUES (%s, %s, %s, %s, %s, %s, %s)
#         """
        
#         if records_to_insert:
#             cursor.executemany(sql, records_to_insert)
#             db.commit()
#             count = len(records_to_insert)
            
#             return jsonify({"message": f"Successfully uploaded {count} legan record(s)!"}), 201
#         else:
#             return jsonify({"message": "No valid legan records found to upload."}), 200

#     except Exception as e:
#         if db.is_connected() and cursor:
#             db.rollback()
        
#         traceback.print_exc() 
#         print("❌ Legan Upload Error:", e)
        
#         return jsonify({"error": f"An error occurred during legan processing: {str(e)}"}), 500

#     finally:
#         if cursor:
#             cursor.close()
        
#         if os.path.exists(filepath):
#             os.remove(filepath)



# @api_routes.route("/api/v1/exams", methods=["GET"])
# def get_all_exams():
#     """Fetch all exam records from the database."""
#     cursor = None
#     try:
#         cursor = db.cursor(dictionary=True)  # Return rows as dicts (column:value)
#         sql = """
#         SELECT 
#             Exam_id, 
#             year, 
#             semester, 
#             type, 
#             period_id, 
#             date, 
#             program AS program, 
#             code_course AS course_code, 
#             day,
#             level
#         FROM exam
#         ORDER BY year DESC, semester ASC, date ASC
#         """
#         cursor.execute(sql)
#         exams = cursor.fetchall()

#         # If no records found
#         if not exams:
#             return jsonify({"message": "No exams found", "data": []}), 200

#         return jsonify({
#             "message": f"Fetched {len(exams)} exam record(s) successfully",
#             "data": exams
#         }), 200

#     except Exception as e:
#         traceback.print_exc()
#         print("❌ Exam Fetch Error:", e)
#         return jsonify({"error": f"Failed to fetch exams: {str(e)}"}), 500

#     finally:
#         if cursor:
#             cursor.close()


# # ======================================================================
# # CRUD ROUTES FOR LEGANS TABLE
# # ======================================================================

# @api_routes.route("/api/v1/legans", methods=["GET"])
# def get_all_legans():
#     """Fetch all legan records."""
#     try:
#         cursor = db.cursor(dictionary=True)
#         cursor.execute("""
#             SELECT Legan_id, legan_name, room_id, level, capacity, full_capacity, program
#             FROM legan
#             ORDER BY level ASC
#         """)
#         legans = cursor.fetchall()
#         return jsonify(legans), 200
#     except Exception as e:
#         traceback.print_exc()
#         return jsonify({"error": str(e)}), 500
#     finally:
#         cursor.close()


# @api_routes.route("/api/v1/legans", methods=["POST"])
# def add_legan():
#     """Add a new legan record."""
#     try:
#         data = request.get_json()
#         required_fields = ["Legan_id", "legan_name", "room_id", "level", "capacity", "full_capacity", "program"]
#         missing = [f for f in required_fields if f not in data or data[f] in [None, ""]]

#         if missing:
#             return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

#         sql = """
#         INSERT INTO legan (Legan_id, legan_name, room_id, level, capacity, full_capacity, program)
#         VALUES (%s, %s, %s, %s, %s, %s, %s)
#         """
#         values = tuple(data[f] for f in required_fields)

#         cursor = db.cursor()
#         cursor.execute(sql, values)
#         db.commit()

#         return jsonify({"message": "✅ Legan added successfully!"}), 201
#     except Exception as e:
#         traceback.print_exc()
#         return jsonify({"error": str(e)}), 500
#     finally:
#         cursor.close()


# @api_routes.route("/api/v1/legans/<Legan_id>", methods=["PUT"])
# def update_legan(Legan_id):
#     """Update an existing legan record."""
#     try:
#         data = request.get_json()
#         fields = ["legan_name", "room_id", "level", "capacity", "full_capacity", "program"]
#         updates = []
#         values = []

#         for field in fields:
#             if field in data:
#                 updates.append(f"{field} = %s")
#                 values.append(data[field])

#         if not updates:
#             return jsonify({"error": "No fields provided for update."}), 400

#         sql = f"UPDATE legan SET {', '.join(updates)} WHERE Legan_id = %s"
#         values.append(Legan_id)

#         cursor = db.cursor()
#         cursor.execute(sql, values)
#         db.commit()

#         if cursor.rowcount == 0:
#             return jsonify({"message": "No legan found with that ID."}), 404

#         return jsonify({"message": "✅ Legan updated successfully!"}), 200
#     except Exception as e:
#         traceback.print_exc()
#         return jsonify({"error": str(e)}), 500
#     finally:
#         cursor.close()


# @api_routes.route("/api/v1/legans/<Legan_id>", methods=["DELETE"])
# def delete_legan(Legan_id):
#     """Delete a legan record by ID."""
#     try:
#         cursor = db.cursor()
#         cursor.execute("DELETE FROM legan WHERE Legan_id = %s", (Legan_id,))
#         db.commit()

#         if cursor.rowcount == 0:
#             return jsonify({"message": "No legan found with that ID."}), 404

#         return jsonify({"message": "🗑️ Legan deleted successfully!"}), 200
#     except Exception as e:
#         traceback.print_exc()
#         return jsonify({"error": str(e)}), 500
#     finally:
#         cursor.close()


# # ======================================================================
# # CRUD ROUTES FOR EXAMS TABLE
# # ======================================================================

# @api_routes.route("/api/v1/exams", methods=["GET"])
# def get_all_exams_new():
#     """Fetch all exams sorted by date ascending."""
#     try:
#         cursor = db.cursor(dictionary=True)
#         cursor.execute("""
#             SELECT Exam_id, year, semester, type, period_id, date, 
#                    program, code_course, day, level
#             FROM exam
#             ORDER BY date ASC
#         """)
#         exams = cursor.fetchall()
#         return jsonify(exams), 200
#     except Exception as e:
#         traceback.print_exc()
#         return jsonify({"error": str(e)}), 500
#     finally:
#         cursor.close()


# @api_routes.route("/api/v1/exams", methods=["POST"])
# def add_exam():
#     """Add a new exam record."""
#     try:
#         data = request.get_json()
#         required_fields = ["Exam_id", "year", "semester", "type", "period_id", "date", "program", "code_course", "day", "level"]
#         missing = [f for f in required_fields if f not in data or data[f] in [None, ""]]

#         if missing:
#             return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

#         sql = """
#         INSERT INTO exam (Exam_id, year, semester, type, period_id, date, program, code_course, day, level)
#         VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
#         """
#         values = tuple(data[f] for f in required_fields)

#         cursor = db.cursor()
#         cursor.execute(sql, values)
#         db.commit()

#         return jsonify({"message": "✅ Exam added successfully!"}), 201
#     except Exception as e:
#         traceback.print_exc()
#         return jsonify({"error": str(e)}), 500
#     finally:
#         cursor.close()


# @api_routes.route("/api/v1/exams/<Exam_id>", methods=["PUT"])
# def update_exam(Exam_id):
#     """Update an existing exam record."""
#     try:
#         data = request.get_json()
#         fields = ["year", "semester", "type", "period_id", "date", "program", "code_course", "day", "level"]
#         updates, values = [], []

#         for field in fields:
#             if field in data:
#                 updates.append(f"{field} = %s")
#                 values.append(data[field])

#         if not updates:
#             return jsonify({"error": "No fields provided for update."}), 400

#         sql = f"UPDATE exam SET {', '.join(updates)} WHERE Exam_id = %s"
#         values.append(Exam_id)

#         cursor = db.cursor()
#         cursor.execute(sql, values)
#         db.commit()

#         if cursor.rowcount == 0:
#             return jsonify({"message": "No exam found with that ID."}), 404

#         return jsonify({"message": "✅ Exam updated successfully!"}), 200
#     except Exception as e:
#         traceback.print_exc()
#         return jsonify({"error": str(e)}), 500
#     finally:
#         cursor.close()


# @api_routes.route("/api/v1/exams/<Exam_id>", methods=["DELETE"])
# def delete_exam(Exam_id):
#     """Delete an exam by ID."""
#     try:
#         cursor = db.cursor()
#         cursor.execute("DELETE FROM exam WHERE Exam_id = %s", (Exam_id,))
#         db.commit()

#         if cursor.rowcount == 0:
#             return jsonify({"message": "No exam found with that ID."}), 404

#         return jsonify({"message": "🗑️ Exam deleted successfully!"}), 200
#     except Exception as e:
#         traceback.print_exc()
#         return jsonify({"error": str(e)}), 500
#     finally:
#         cursor.close()


# # ======================================================================
# # CRUD ROUTES FOR ROOMS TABLE
# # ======================================================================

# @api_routes.route("/api/v1/rooms", methods=["GET"])
# def get_all_rooms():
#     """Fetch all rooms sorted by floor."""
#     try:
#         cursor = db.cursor(dictionary=True)
#         cursor.execute("""
#             SELECT room_id, room_name, capacity, floor
#             FROM rooms
#             ORDER BY floor ASC
#         """)
#         rooms = cursor.fetchall()
#         return jsonify(rooms), 200
#     except Exception as e:
#         traceback.print_exc()
#         return jsonify({"error": str(e)}), 500
#     finally:
#         cursor.close()


# @api_routes.route("/api/v1/rooms", methods=["POST"])
# def add_room():
#     """Add a new room record."""
#     try:
#         data = request.get_json()
#         required_fields = ["room_name", "capacity", "floor"]
#         missing = [f for f in required_fields if f not in data or data[f] in [None, ""]]

#         if missing:
#             return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

#         sql = "INSERT INTO rooms (room_name, capacity, floor) VALUES (%s, %s, %s)"
#         values = tuple(data[f] for f in required_fields)

#         cursor = db.cursor()
#         cursor.execute(sql, values)
#         db.commit()

#         return jsonify({"message": "✅ Room added successfully!"}), 201
#     except Exception as e:
#         traceback.print_exc()
#         return jsonify({"error": str(e)}), 500
#     finally:
#         cursor.close()


# @api_routes.route("/api/v1/rooms/<int:room_id>", methods=["PUT"])
# def update_room(room_id):
#     """Update a room record by ID."""
#     try:
#         data = request.get_json()
#         fields = ["room_name", "capacity", "floor"]
#         updates, values = [], []

#         for field in fields:
#             if field in data:
#                 updates.append(f"{field} = %s")
#                 values.append(data[field])

#         if not updates:
#             return jsonify({"error": "No fields provided for update."}), 400

#         sql = f"UPDATE rooms SET {', '.join(updates)} WHERE room_id = %s"
#         values.append(room_id)

#         cursor = db.cursor()
#         cursor.execute(sql, values)
#         db.commit()

#         if cursor.rowcount == 0:
#             return jsonify({"message": "No room found with that ID."}), 404

#         return jsonify({"message": "✅ Room updated successfully!"}), 200
#     except Exception as e:
#         traceback.print_exc()
#         return jsonify({"error": str(e)}), 500
#     finally:
#         cursor.close()


# @api_routes.route("/api/v1/rooms/<int:room_id>", methods=["DELETE"])
# def delete_room(room_id):
#     """Delete a room by ID."""
#     try:
#         cursor = db.cursor()
#         cursor.execute("DELETE FROM rooms WHERE room_id = %s", (room_id,))
#         db.commit()

#         if cursor.rowcount == 0:
#             return jsonify({"message": "No room found with that ID."}), 404

#         return jsonify({"message": "🗑️ Room deleted successfully!"}), 200
#     except Exception as e:
#         traceback.print_exc()
#         return jsonify({"error": str(e)}), 500
#     finally:
#         cursor.close()





# @api_routes.route("/api/v1/students-legans", methods=["GET"])
# def get_all_students_legans():
#     """Fetch all exam (students-legans) records sorted by date ascending."""
#     try:
#         cursor = db.cursor(dictionary=True)
#         cursor.execute("""
#             SELECT Exam_id, year, semester, type, period_id, date,
#                    program, code_course, day, level
#             FROM exam
#             ORDER BY date ASC
#         """)
#         exams = cursor.fetchall()
#         return jsonify(exams), 200
#     except Exception as e:
#         traceback.print_exc()
#         return jsonify({"error": str(e)}), 500
#     finally:
#         cursor.close()


# @api_routes.route("/api/v1/students-legans", methods=["POST"])
# def add_students_legan():
#     """Add a new exam record (students-legans)."""
#     try:
#         data = request.get_json()
#         required = ["Exam_id", "year", "semester", "type", "period_id", "date", "program", "code_course", "day", "level"]

#         missing = [f for f in required if f not in data or data[f] in [None, ""]]
#         if missing:
#             return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

#         sql = """
#         INSERT INTO exam (
#             Exam_id, year, semester, type, period_id, date,
#             program, code_course, day, level
#         ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
#         """
#         values = tuple(data[f] for f in required)

#         cursor = db.cursor()
#         cursor.execute(sql, values)
#         db.commit()

#         return jsonify({"message": "✅ Students-Legan record added successfully!"}), 201
#     except Exception as e:
#         traceback.print_exc()
#         return jsonify({"error": str(e)}), 500
#     finally:
#         cursor.close()


# @api_routes.route("/api/v1/students-legans/<Exam_id>", methods=["PUT"])
# def update_students_legan(Exam_id):
#     """Update exam (students-legans) entry by Exam_id."""
#     try:
#         data = request.get_json()
#         fields = ["year", "semester", "type", "period_id", "date", "program", "code_course", "day", "level"]

#         updates, values = [], []
#         for f in fields:
#             if f in data:
#                 updates.append(f"{f} = %s")
#                 values.append(data[f])

#         if not updates:
#             return jsonify({"error": "No fields provided for update"}), 400

#         sql = f"UPDATE exam SET {', '.join(updates)} WHERE Exam_id = %s"
#         values.append(Exam_id)

#         cursor = db.cursor()
#         cursor.execute(sql, values)
#         db.commit()

#         if cursor.rowcount == 0:
#             return jsonify({"message": "No record found for that Exam_id"}), 404

#         return jsonify({"message": "✅ Students-Legan record updated successfully!"}), 200
#     except Exception as e:
#         traceback.print_exc()
#         return jsonify({"error": str(e)}), 500
#     finally:
#         cursor.close()


# @api_routes.route("/api/v1/students-legans/<Exam_id>", methods=["DELETE"])
# def delete_students_legan(Exam_id):
#     """Delete exam (students-legans) by Exam_id."""
#     try:
#         cursor = db.cursor()
#         cursor.execute("DELETE FROM exam WHERE Exam_id = %s", (Exam_id,))
#         db.commit()

#         if cursor.rowcount == 0:
#             return jsonify({"message": "No record found with that Exam_id"}), 404

#         return jsonify({"message": "🗑️ Students-Legan record deleted successfully!"}), 200
#     except Exception as e:
#         traceback.print_exc()
#         return jsonify({"error": str(e)}), 500
#     finally:
#         cursor.close()


# ===== FILE: api/routes.py =====
from flask import Blueprint, request, jsonify, send_file
from io import BytesIO
import os
import pandas as pd
from werkzeug.utils import secure_filename
import traceback
import db as dbmod

api_routes = Blueprint('api_routes', __name__)
UPLOAD_FOLDER = os.getenv('UPLOAD_FOLDER', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Day ordering as CASE
DAY_ORDER_CASE = "CASE e.day WHEN 'Saturday' THEN 1 WHEN 'Sunday' THEN 2 WHEN 'Monday' THEN 3 WHEN 'Tuesday' THEN 4 WHEN 'Wednesday' THEN 5 WHEN 'Thursday' THEN 6 WHEN 'Friday' THEN 7 END"

PERIOD_MINUTES_SQL_PG = "(( (CASE WHEN CAST(split_part(split_part(e.period_id, '-', 1), ':', 1) AS INTEGER) BETWEEN 1 AND 7 THEN CAST(split_part(split_part(e.period_id, '-', 1), ':', 1) AS INTEGER) + 12 ELSE CAST(split_part(split_part(e.period_id, '-', 1), ':', 1) AS INTEGER) END) * 60) + CAST(split_part(split_part(e.period_id, '-', 1), ':', 2) AS INTEGER) ))"

# --------- helper to build filters ----------
def build_filters(prefix='e'):
    where, params = [], []
    def add(col, qp):
        v = request.args.get(qp)
        if v:
            where.append(f"{prefix}.{col} = %s")
            params.append(v)
    for col, qp in [('program','program'),('level','level'),('code_course','code_course'),('day','day'),('type','type'),('period_id','period_id'),('date','date')]:
        add(col, qp)
    ex = request.args.get('exam_id')
    if ex:
        where.append(f"{prefix}.Exam_id = %s")
        params.append(ex)
    return (f"WHERE {' AND '.join(where)}" if where else ''), params

# ============ simple endpoints (rooms) ==========
@api_routes.route('/api/hello')
def hello():
    return jsonify({'message':'Hello from Flask (Postgres)'}), 200

@api_routes.route('/api/v1/rooms', methods=['GET'])
def get_all_rooms():
    try:
        rows = dbmod.fetchall('SELECT room_id, room_name, capacity, floor FROM rooms ORDER BY floor ASC')
        return jsonify(rows), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@api_routes.route('/api/v1/rooms', methods=['POST'])
def add_room():
    try:
        data = request.get_json(force=True)
        required = ['room_name','capacity','floor']
        if any(data.get(k) in (None,'') for k in required):
            return jsonify({'error':'Missing fields'}), 400
        dbmod.execute('INSERT INTO rooms (room_name, capacity, floor) VALUES (%s,%s,%s)', (data['room_name'], data['capacity'], data['floor']))
        return jsonify({'message':'✅ Room added successfully!'}), 201
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@api_routes.route('/api/v1/rooms/upload', methods=['POST'])
def upload_room_file():
    if 'file' not in request.files:
        return jsonify({'error':'No file part'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error':'No file selected'}), 400
    filename = secure_filename(file.filename)
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    try:
        file.save(filepath)
        df = pd.read_excel(filepath)
        required = {'room_name','capacity','floor'}
        if not required.issubset(df.columns):
            missing = required - set(df.columns)
            return jsonify({'error': f'Missing columns: {', '.join(missing)}'}), 400
        batch = []
        for _, row in df.iterrows():
            try:
                capacity = int(row.get('capacity',0))
            except Exception:
                continue
            room_name = str(row.get('room_name','')).strip()
            floor = str(row.get('floor','')).strip()
            if not room_name or capacity <= 0:
                continue
            batch.append((room_name, capacity, floor))
        if batch:
            with dbmod.get_cursor(False) as (conn, cur):
                cur.executemany('INSERT INTO rooms (room_name, capacity, floor) VALUES (%s,%s,%s)', batch)
                conn.commit()
            return jsonify({'message': f'Successfully uploaded {len(batch)} rooms!'}), 201
        return jsonify({'message':'No valid room records found'}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        try:
            if os.path.exists(filepath): os.remove(filepath)
        except Exception:
            pass

# ============ students-legans grouped ==========
@api_routes.route('/api/v1/students-legans', methods=['GET'])
def get_all_students_legans():
    try:
        where_sql, params = build_filters('e')
        sql = f"""
        SELECT e.Exam_id, e.year, e.semester, e.type, e.program, e.code_course,
               e.date, e.day, e.level, e.period_id, e.assigned,
               l.Legan_id AS legan_id, l.legan_name, l.capacity AS legan_capacity, r.room_name
        FROM exam e
        LEFT JOIN student_legan sl ON sl.Exam = e.Exam_id
        LEFT JOIN legan l ON l.Legan_id = sl.legan_id
        LEFT JOIN rooms r ON r.room_id = l.room_id
        {where_sql}
        ORDER BY {DAY_ORDER_CASE}, {PERIOD_MINUTES_SQL_PG}, e.Exam_id
        """
        rows = dbmod.fetchall(sql, params)
        grouped = {}
        for row in rows:
            eid = row['Exam_id']
            if eid not in grouped:
                grouped[eid] = {
                    'Exam_id': eid,
                    'year': row['year'],
                    'semester': row['semester'],
                    'type': row['type'],
                    'program': row['program'],
                    'code_course': row['code_course'],
                    'date': str(row['date']) if row['date'] else None,
                    'day': row['day'],
                    'level': row['level'],
                    'period_id': row['period_id'],
                    'assigned': int(row.get('assigned') or 0),
                    'legans': []
                }
            if row.get('legan_id'):
                grouped[eid]['legans'].append({
                    'legan_id': row['legan_id'],
                    'legan_name': row['legan_name'],
                    'room_name': row['room_name'],
                    'capacity': row['legan_capacity']
                })
        return jsonify(list(grouped.values())), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

# ============ assign endpoint ===========
@api_routes.route('/api/v1/assign/<int:exam_id>', methods=['POST'])
def assign_course(exam_id):
    try:
        with dbmod.get_cursor(True) as (conn, cur):
            cur.execute('SELECT * FROM exam WHERE Exam_id=%s', (exam_id,))
            exam = cur.fetchone()
            if not exam:
                return jsonify({'error':'Exam not found'}), 404
            program = str(exam['program']).upper()
            level = str(exam['level'])
            course = str(exam['code_course'])

            cur.execute('SELECT Legan_id, legan_name, room_id, level, capacity, program FROM legan WHERE program=%s AND level=%s ORDER BY Legan_id ASC', (program, level))
            legans = cur.fetchall()
            if not legans:
                return jsonify({'error':'No legans available for this program/level'}), 400

            cur.execute('SELECT student_ID, student_name FROM registration WHERE program=%s AND course=%s ORDER BY level ASC, student_ID', (program, course))
            students = cur.fetchall()
            if not students:
                return jsonify({'error':'No registered students for this course'}), 400

            inserted = 0
            idx = 0
            total = len(students)

            for leg in legans:
                cap = int(leg.get('capacity') or 0)
                for _ in range(cap):
                    if idx >= total:
                        break
                    s = students[idx]
                    idx += 1
                    cur.execute('INSERT INTO student_legan (legan_id, student_id, Exam) VALUES (%s,%s,%s)', (leg['Legan_id'], s['student_ID'], exam_id))
                    cur.execute("INSERT INTO student_legan_history (legan_id, student_id, exam_id, action) VALUES (%s,%s,%s,%s)", (leg['Legan_id'], s['student_ID'], exam_id, 'ASSIGNED'))
                    inserted += 1
                if idx >= total:
                    break

            if inserted > 0:
                cur.execute('UPDATE exam SET assigned=1 WHERE Exam_id=%s', (exam_id,))
        return jsonify({'message': f'✅ Assigned {inserted}/{total} students.','assigned': inserted,'total': total}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

# ============ reassign endpoint (new students only) ===========
@api_routes.route('/api/v1/reassign/<int:exam_id>', methods=['POST'])
def reassign_new_students(exam_id):
    try:
        with dbmod.get_cursor(True) as (conn, cur):
            cur.execute('SELECT * FROM exam WHERE Exam_id=%s', (exam_id,))
            exam = cur.fetchone()
            if not exam:
                return jsonify({'error':'Exam not found'}), 404
            program = str(exam['program']).upper()
            level = str(exam['level'])
            course = str(exam['code_course'])

            cur.execute('SELECT Legan_id, legan_name, room_id, level, capacity, program FROM legan WHERE program=%s AND level=%s ORDER BY Legan_id ASC', (program, level))
            legans = cur.fetchall()
            if not legans:
                return jsonify({'error':'No legans available for this program/level'}), 400

            cur.execute('SELECT student_ID, student_name FROM registration WHERE program=%s AND course=%s ORDER BY level ASC, student_ID', (program, course))
            all_students = cur.fetchall()
            if not all_students:
                return jsonify({'error':'No students registered for this course'}), 400

            cur.execute('SELECT student_id FROM student_legan WHERE Exam=%s', (exam_id,))
            assigned = cur.fetchall()
            assigned_ids = {r['student_id'] for r in assigned}

            new_students = [s for s in all_students if s['student_ID'] not in assigned_ids]
            if not new_students:
                return jsonify({'message':'✅ No new students found. All already assigned.'}), 200

            # last used legan
            cur.execute('SELECT legan_id FROM student_legan WHERE Exam=%s ORDER BY student_Legan_id DESC LIMIT 1', (exam_id,))
            last_used = cur.fetchone()
            last_used_id = last_used['legan_id'] if last_used else None

            start_index = 0
            if last_used_id:
                for i, leg in enumerate(legans):
                    if leg['Legan_id'] == last_used_id:
                        start_index = i
                        break

            inserted = 0
            idx = 0
            total_new = len(new_students)

            # fill from start_index then wrap
            for i in range(start_index, len(legans)):
                leg = legans[i]
                cap = int(leg.get('capacity') or 0)
                cur.execute('SELECT COUNT(*) AS c FROM student_legan WHERE legan_id=%s AND Exam=%s', (leg['Legan_id'], exam_id))
                used = cur.fetchone()['c']
                free = max(0, cap - used)
                for _ in range(free):
                    if idx >= total_new:
                        break
                    s = new_students[idx]
                    idx += 1
                    cur.execute('INSERT INTO student_legan (legan_id, student_id, Exam) VALUES (%s,%s,%s)', (leg['Legan_id'], s['student_ID'], exam_id))
                    cur.execute("INSERT INTO student_legan_history (legan_id, student_id, exam_id, action) VALUES (%s,%s,%s,%s)", (leg['Legan_id'], s['student_ID'], exam_id, 'REASSIGNED'))
                    inserted += 1
                if idx >= total_new:
                    break
            if idx < total_new:
                for i in range(0, start_index):
                    leg = legans[i]
                    cap = int(leg.get('capacity') or 0)
                    cur.execute('SELECT COUNT(*) AS c FROM student_legan WHERE legan_id=%s AND Exam=%s', (leg['Legan_id'], exam_id))
                    used = cur.fetchone()['c']
                    free = max(0, cap - used)
                    for _ in range(free):
                        if idx >= total_new:
                            break
                        s = new_students[idx]
                        idx += 1
                        cur.execute('INSERT INTO student_legan (legan_id, student_id, Exam) VALUES (%s,%s,%s)', (leg['Legan_id'], s['student_ID'], exam_id))
                        cur.execute("INSERT INTO student_legan_history (legan_id, student_id, exam_id, action) VALUES (%s,%s,%s,%s)", (leg['Legan_id'], s['student_ID'], exam_id, 'REASSIGNED'))
                        inserted += 1
                    if idx >= total_new:
                        break

            return jsonify({'message': f'🔄 Reassigned {inserted}/{total_new} new students.', 'new_assigned': inserted, 'new_total': total_new, 'started_from_legan': last_used_id or legans[0]['Legan_id']}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

# ============ unassign endpoint ============
@api_routes.route('/api/v1/unassign/<int:exam_id>', methods=['POST'])
def unassign_course(exam_id):
    try:
        with dbmod.get_cursor(True) as (conn, cur):
            cur.execute('SELECT * FROM exam WHERE Exam_id=%s', (exam_id,))
            exam = cur.fetchone()
            if not exam:
                return jsonify({'error':'Exam not found'}), 404
            cur.execute('SELECT legan_id, student_id FROM student_legan WHERE Exam=%s', (exam_id,))
            rows = cur.fetchall()
            if not rows:
                return jsonify({'message':'No students assigned for this exam'}), 200
            for r in rows:
                cur.execute("INSERT INTO student_legan_history (legan_id, student_id, exam_id, action) VALUES (%s,%s,%s,%s)", (r['legan_id'], r['student_id'], exam_id, 'UNASSIGNED'))
            cur.execute('DELETE FROM student_legan WHERE Exam=%s', (exam_id,))
            cur.execute('UPDATE exam SET assigned=0 WHERE Exam_id=%s', (exam_id,))
            return jsonify({'message': f'✅ Unassigned {len(rows)} students.', 'unassigned': len(rows)}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

# ============ print JSON (grouped) ============
@api_routes.route('/api/v1/students-legans/print', methods=['GET'])
def print_students_legans_json():
    try:
        where_sql, params = build_filters('e')
        sql = f"""
            SELECT
              e.Exam_id, e.year, e.program AS program_id, e.level AS exam_level, e.code_course AS course,
              e.day, e.period_id, e.date, e.type,
              l.Legan_id AS legan_id, l.legan_name, l.capacity,
              r.room_name, r.floor AS floor,
              p.logo AS program_logo,
              p.arabic_name AS program_arabic_name,
              p.English_name AS program_english_name,
              sl.student_legan_id, s.student_ID, s.student_name,s.payment,s.level
            FROM exam e
            LEFT JOIN student_legan sl ON sl.Exam=e.Exam_id
            LEFT JOIN legan l ON l.Legan_id=sl.legan_id
            LEFT JOIN rooms r ON r.room_id=l.room_id
            LEFT JOIN programs p ON p.program_id=e.program
            LEFT JOIN registration s ON s.student_ID=sl.student_id
            {where_sql}
            ORDER BY {DAY_ORDER_CASE}, {PERIOD_MINUTES_SQL_PG}, e.Exam_id, l.Legan_id, s.student_ID
        """
        rows = dbmod.fetchall(sql, params)
        bucket = {}
        for row in rows:
            if not row.get('legan_id'):
                continue
            key = f"{row['Exam_id']}_{row['legan_id']}"
            if key not in bucket:
                bucket[key] = {
                    'exam_id': row['Exam_id'],
                    'legan_id': row['legan_id'],
                    'legan_name': row['legan_name'],
                    'room_name': row['room_name'],
                    'floor': row['floor'],
                    'capacity': row['capacity'],
                    'level': row['exam_level'],
                    'course': row['course'],
                    'day': row['day'],
                    'period_id': row['period_id'],
                    'date': str(row['date']) if row['date'] else None,
                    'students': []
                }
            if row.get('student_ID'):
                students = bucket[key]['students']
                if not any(s['student_legan_id'] == row['student_legan_id'] for s in students):
                    students.append({'student_legan_id': row['student_legan_id'], 'student_id': row['student_ID'], 'student_name': row['student_name'], 'payment': row['payment'], 'level': row['level']})
        legans = list(bucket.values())
        return jsonify({'legans': legans, 'exam_info': {'type': (rows[0]['type'] if rows else None), 'program': (rows[0]['program_id'] if rows else None),'year': (rows[0]['year'] if rows else None)}}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

# ============ print PDF (simple) ============
@api_routes.route('/api/v1/students-legans/print/pdf', methods=['GET'])
def print_students_legans_pdf():
    try:
        exam_id = request.args.get('exam_id')
        if not exam_id:
            return jsonify({'error':'exam_id is required'}), 400
        where_sql, params = build_filters('e')
        sql = f"""
            SELECT e.Exam_id, e.year, e.program AS program_id, e.level, e.code_course AS course,
                   e.day, e.period_id, e.date, e.type,
                   l.Legan_id AS legan_id, l.legan_name, l.capacity,
                   r.room_name, r.floor AS floor,
                   sl.student_legan_id, s.student_ID, s.student_name
            FROM exam e
            LEFT JOIN student_legan sl ON sl.Exam=e.Exam_id
            LEFT JOIN legan l ON l.Legan_id=sl.legan_id
            LEFT JOIN rooms r ON r.room_id=l.room_id
            LEFT JOIN registration s ON s.student_ID=sl.student_id
            WHERE e.Exam_id = %s
            ORDER BY {DAY_ORDER_CASE}, {PERIOD_MINUTES_SQL_PG}, e.Exam_id, l.Legan_id, s.student_ID
        """
        rows = dbmod.fetchall(sql, (exam_id,))
        bucket = {}
        for row in rows:
            if not row.get('legan_id'):
                continue
            key = f"{row['Exam_id']}_{row['legan_id']}"
            bucket.setdefault(key, {'legan_id': row['legan_id'], 'legan_name': row['legan_name'], 'room_name': row['room_name'], 'floor': row['floor'], 'capacity': row['capacity'], 'students': []})
            if row.get('student_ID'):
                bucket[key]['students'].append({'id': row['student_ID'], 'name': row['student_name']})
        # build PDF using reportlab if available
        try:
            from reportlab.pdfgen import canvas
            from reportlab.lib.pagesizes import A4
        except Exception:
            return jsonify({'error':'PDF dependency not installed'}), 501
        pdf_buffer = BytesIO()
        p = canvas.Canvas(pdf_buffer)
        width, height = A4
        for key, data in bucket.items():
            p.setFont('Helvetica-Bold', 14)
            p.drawString(40, height-50, f"Legen: {data['legan_name']}")
            y = height-80
            p.setFont('Helvetica', 11)
            for s in data['students']:
                p.drawString(50, y, f"{s['id']} - {s['name']}")
                y -= 16
                if y < 80:
                    p.showPage()
                    y = height-80
            p.showPage()
        p.save()
        pdf_buffer.seek(0)
        return send_file(pdf_buffer, as_attachment=True, download_name=f'exam_{exam_id}_legans.pdf', mimetype='application/pdf')
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# End of project files

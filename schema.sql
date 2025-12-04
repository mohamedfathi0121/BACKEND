-- schema.sql

CREATE TABLE IF NOT EXISTS doctors (
    id SERIAL PRIMARY KEY,
    doctor_name VARCHAR(255),
    email VARCHAR(255),
    phone VARCHAR(50)
);

CREATE TABLE IF NOT EXISTS rooms (
    room_id SERIAL PRIMARY KEY,
    room_name VARCHAR(255),
    capacity INTEGER,
    floor VARCHAR(50)
);

CREATE TABLE IF NOT EXISTS courses (
    id SERIAL PRIMARY KEY,
    course_code VARCHAR(50),
    course_name VARCHAR(255),
    course_arab_name VARCHAR(255),
    credit_hrs INTEGER,
    prerequisite VARCHAR(255),
    type VARCHAR(50),
    lab VARCHAR(50)
);

CREATE TABLE IF NOT EXISTS students (
    id SERIAL PRIMARY KEY,
    student_ID VARCHAR(64),
    NID VARCHAR(64),
    Arab_name VARCHAR(255),
    Eng_name VARCHAR(255),
    HNU_email VARCHAR(255),
    phone_number VARCHAR(50),
    parent_number VARCHAR(50),
    address VARCHAR(255),
    medical_status VARCHAR(255)
);

CREATE TABLE IF NOT EXISTS registration (
    id SERIAL PRIMARY KEY,
    NID VARCHAR(64),
    level VARCHAR(50),
    student_ID VARCHAR(64),
    course VARCHAR(64),
    student_group VARCHAR(64),
    student_name VARCHAR(255),
    payment VARCHAR(32),
    program VARCHAR(64),
    notes TEXT
);

CREATE TABLE IF NOT EXISTS exam (
    Exam_id INTEGER PRIMARY KEY,
    year VARCHAR(10),
    semester VARCHAR(10),
    type VARCHAR(10),
    period_id VARCHAR(50),
    date VARCHAR(50),
    program VARCHAR(50),
    code_course VARCHAR(50),
    day VARCHAR(20),
    level VARCHAR(20),
    assigned INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS legan (
    Legan_id INTEGER PRIMARY KEY,
    legan_name VARCHAR(255),
    room_id INTEGER,
    level VARCHAR(50),
    capacity INTEGER,
    full_capacity INTEGER,
    program VARCHAR(50)
);

CREATE TABLE IF NOT EXISTS student_legan (
    student_Legan_id SERIAL PRIMARY KEY,
    legan_id INTEGER,
    student_id VARCHAR(64),
    exam INTEGER
);

CREATE TABLE IF NOT EXISTS student_legan_history (
    id SERIAL PRIMARY KEY,
    legan_id INTEGER,
    student_id VARCHAR(64),
    exam_id INTEGER,
    action VARCHAR(32),
    created_at TIMESTAMP DEFAULT NOW()
);

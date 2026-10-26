from flask import Flask, request,session,render_template,redirect,flash,url_for
from flask import send_from_directory, abort
import mysql.connector
import os
import uuid
from datetime import date, datetime
import secrets
from decimal import Decimal, InvalidOperation


app = Flask(__name__)

app.secret_key = "freelancer_hub_secret"

# ---------- RESUME UPLOAD SETTINGS ----------
UPLOAD_FOLDER = os.path.join(app.root_path, "uploads", "resumes")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024   # max 2 MB


def allowed_resume(filename):
    return filename.lower().endswith(".pdf")


# =====================================================
# DATABASE
# =====================================================

def get_db_connection():
    return mysql.connector.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", "3306")),
        user=os.environ.get("DB_USER", "root"),
        password=os.environ.get("DB_PASSWORD", ""),
        database=os.environ.get("DB_NAME", "freelancer_hub"),
        ssl_ca=os.environ.get("DB_SSL_CA") or None
    )

def create_notification(user_id, title, message):

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute( """
            INSERT INTO notifications
            (user_id, title, message)
            VALUES (%s, %s, %s)
        """, (
            user_id,
            title,
            message
        ))

        conn.commit()

    except mysql.connector.Error as error:
        conn.rollback()
        print("Notification Error:", error)

    finally:
        cursor.close()
        conn.close()

@app.route("/delete-notification/<int:notification_id>", methods=["POST"])
def delete_notification(notification_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            DELETE FROM notifications
            WHERE id = %s AND user_id = %s
        """, (notification_id, user_id))

        conn.commit()

    except mysql.connector.Error as error:
        print("Delete Notification Error:", error)

    finally:
        cursor.close()
        conn.close()

    return redirect(request.referrer or url_for("freelancer_dashboard"))


# =====================================================
# HOME
# =====================================================

@app.route("/")
def home():
    return render_template("home.html")

# =====================================================
# LOGIN
# =====================================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:

            cursor.execute("""
                SELECT id, name, email, password, role
                FROM users
                WHERE email = %s
            """, (email,))

            user = cursor.fetchone()

            if user is None:
                flash("Email not registered!", "danger")
                return redirect(url_for("login"))

            if user["password"] != password:
                flash("Incorrect password!", "danger")
                return redirect(url_for("login"))

            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["role"] = user["role"]

            if user["role"].lower() == "freelancer":
                return redirect(url_for("freelancer_dashboard"))
            else:
                return redirect(url_for("client_dashboard"))

        except mysql.connector.Error as error:

            print("Login Error:", error)

            flash(
                "Error while logging in.",
                "danger"
            )

            return redirect(url_for("login"))

        finally:

            cursor.close()
            conn.close()

    return render_template("login.html")


# =====================================================
# SIGNUP
# =====================================================

@app.route("/signup", methods=["GET", "POST"])
def signup():

    if request.method == "POST":

        name = request.form["name"]
        email = request.form["email"]
        skills = request.form.get("skills", "")
        password = request.form["password"]
        role = request.form["role"]

        conn = get_db_connection()
        cursor = conn.cursor()

        try:

            cursor.execute("""
                SELECT id
                FROM users
                WHERE email = %s
            """, (email,))

            existing_user = cursor.fetchone()

            if existing_user:
                flash("Email already registered!", "danger")
                return redirect(url_for("signup"))

            cursor.execute("""
                INSERT INTO users
                (name, email, skills, password, role)
                VALUES (%s, %s, %s, %s, %s)
            """, (name, email, skills, password, role))
            conn.commit()

            user_id = cursor.lastrowid

            session["user_id"] = user_id
            session["user_name"] = name
            session["role"] = role

            if role.lower() == "freelancer":
                return redirect(url_for("freelancer_dashboard"))
            else:
                return redirect(url_for("client_dashboard"))

        except mysql.connector.Error as error:

            conn.rollback()

            print("Signup Error:", error)

            flash(
                "Error while creating account.",
                "danger"
            )

            return redirect(url_for("signup"))

        finally:

            cursor.close()
            conn.close()

    return render_template("signup.html")


# =====================================================
# CLIENT DASHBOARD
# =====================================================

@app.route("/client-dashboard")
def client_dashboard():

    if "user_id" not in session:
        return redirect(url_for("signup"))

    client_id = session["user_id"]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    preferences = {
        "show_dashboard_statistics": 1,
        "show_recent_activity": 1
    }

    jobs = []
    recent_activity = []

    try:

        # Get client account preferences
        cursor.execute("""
            SELECT
                show_dashboard_statistics,
                show_recent_activity
            FROM users
            WHERE id = %s
        """, (client_id,))

        result = cursor.fetchone()

        if result:
            preferences = result

        # Client ने post केलेले projects
        cursor.execute("""
            SELECT
                id,
                client_id,
                title,
                description,
                skills,
                budget,
                deadline,
                created_at
            FROM jobs
            WHERE client_id = %s
            ORDER BY id DESC
        """, (client_id,))

        jobs = cursor.fetchall()

        # Recent activity = latest 5 projects
        recent_activity = jobs[:5]

    except mysql.connector.Error as error:

        print("Database Error:", error)

        jobs = []
        recent_activity = []

    finally:

        cursor.close()
        conn.close()


    # -----------------------------
    # DASHBOARD DATA
    # -----------------------------

    total_projects = len(jobs)

    total_budget = sum(
        float(job["budget"] or 0)
        for job in jobs
    )

    active_projects = 0
    overdue_projects = 0

    today = date.today()


    # Active / Overdue calculation

    for job in jobs:

        deadline = job.get("deadline")

        # Deadline नसल्यास Active
        if not deadline:

            active_projects += 1

            continue


        # MySQL date/datetime handle करणे
        if hasattr(deadline, "date"):

            deadline_date = deadline.date()

        else:

            try:

                deadline_date = datetime.strptime(
                    str(deadline),
                    "%Y-%m-%d"
                ).date()

            except ValueError:

                active_projects += 1

                continue


        # आज किंवा पुढची deadline = Active
        if deadline_date >= today:

            active_projects += 1

        # आजच्या आधीची deadline = Overdue
        else:

            overdue_projects += 1

    return render_template(
        "client_dashboard.html",
        jobs=jobs,
        total_projects=total_projects,
        active_projects=active_projects,
        total_budget=total_budget,
        overdue_projects=overdue_projects,
        today=today,
        show_dashboard_statistics=preferences["show_dashboard_statistics"],
        show_recent_activity=preferences["show_recent_activity"],
        recent_activity=recent_activity
    )


# =====================================================
# POST JOB
# =====================================================

@app.route("/post-job", methods=["GET", "POST"])
def post_job():

    if "user_id" not in session:

        return redirect(url_for("signup"))


    if request.method == "POST":

        title = request.form["title"]

        description = request.form["description"]

        skills = request.form["skills"]

        budget = request.form["budget"]

        deadline = request.form["deadline"]

        client_id = session["user_id"]


        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)


        try:

            cursor.execute("""
                INSERT INTO jobs
                (
                    client_id,
                    title,
                    description,
                    skills,
                    budget,
                    deadline
                )
                VALUES
                (%s, %s, %s, %s, %s, %s)
            """, (
                client_id,
                title,
                description,
                skills,
                budget,
                deadline
            ))

            conn.commit()

            # CREATE NEW PROJECT NOTIFICATIONS
            cursor.execute("""
                SELECT id
                FROM users
                WHERE LOWER(TRIM(role)) = 'freelancer'
                AND project_notifications = 1
            """)

            freelancers = cursor.fetchall()

            for freelancer in freelancers:
                create_notification(
                    freelancer["id"],
                    "New Project Available",
                    f"New project '{title}' has been posted by a client."
                )

            flash(
                "Project posted successfully!",
                "success"
            )

        except mysql.connector.Error as error:

            conn.rollback()

            print("Database Error:", error)

            flash(
                "Error while posting project.",
                "danger"
            )

        finally:

            cursor.close()
            conn.close()


        return redirect(
            url_for("client_dashboard")
        )


    return render_template("post_job.html")


# =====================================================
# VIEW JOB
# =====================================================

@app.route("/view-job/<int:job_id>")
def view_job(job_id):

    if "user_id" not in session:

        return redirect(url_for("signup"))


    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:

        cursor.execute("""
            SELECT *
            FROM jobs
            WHERE id = %s
            AND client_id = %s
        """, (
            job_id,
            session["user_id"]
        ))

        job = cursor.fetchone()

    except mysql.connector.Error as error:

        print("View Job Error:", error)

        return f"Database Error: {error}"

    finally:

        cursor.close()
        conn.close()


    if job is None:

        return "Project not found", 404


    return render_template(
        "view_job.html",
        job=job
    )


# =====================================================
# CLIENT PROFILE
# =====================================================

@app.route("/client-profile")
def client_profile():

    if "user_id" not in session:

        return redirect(url_for("signup"))


    user_id = session["user_id"]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:

        cursor.execute("""
            SELECT id, name, email, role
            FROM users
            WHERE id = %s
        """, (user_id,))

        user = cursor.fetchone()


        if user is None:

            return "User not found", 404


        cursor.execute("""
            SELECT
                id,
                title,
                description,
                skills,
                budget,
                deadline
            FROM jobs
            WHERE client_id = %s
            ORDER BY id DESC
        """, (user_id,))

        jobs = cursor.fetchall()

    finally:

        cursor.close()
        conn.close()


    return render_template(
        "client_profile.html",
        user=user,
        jobs=jobs
    )


# =====================================================
# FIND FREELANCER
# =====================================================

@app.route("/find-freelancer")
def find_freelancer():

    if "user_id" not in session:

        return redirect(url_for("signup"))


    search = request.args.get(
        "search",
        ""
    ).strip()


    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:

        if search:

            cursor.execute("""
                SELECT
                    id,
                    name,
                    email,
                    role
                FROM users
                WHERE LOWER(TRIM(role)) = 'freelancer'
                AND (
                    name LIKE %s
                    OR email LIKE %s
                )
                ORDER BY id DESC
            """, (
                f"%{search}%",
                f"%{search}%"
            ))

        else:

            cursor.execute("""
                SELECT
                    id,
                    name,
                    email,
                    role
                FROM users
                WHERE LOWER(TRIM(role)) = 'freelancer'
                ORDER BY id DESC
            """)

        freelancers = cursor.fetchall()

    except mysql.connector.Error as error:

        print("Database Error:", error)

        freelancers = []

    finally:

        cursor.close()
        conn.close()


    return render_template(
        "find_freelancer.html",
        freelancers=freelancers,
        search=search
    )


# =====================================================
# VIEW FREELANCER
# =====================================================

@app.route("/view-freelancer/<int:freelancer_id>")
def view_freelancer(freelancer_id):

    if "user_id" not in session:
        return redirect(url_for("signup"))

    client_id = session["user_id"]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:

        # Freelancer information
        cursor.execute("""
            SELECT
                id,
                name,
                email,
                role,
                resume_file
            FROM users
            WHERE id = %s
            AND LOWER(TRIM(role)) = 'freelancer'
        """, (freelancer_id,))

        freelancer = cursor.fetchone()

        if freelancer is None:
            return "Freelancer not found", 404

        # Client's projects
        cursor.execute("""
            SELECT
                id,
                title
            FROM jobs
            WHERE client_id = %s
            ORDER BY created_at DESC
        """, (client_id,))

        jobs = cursor.fetchall()

    except mysql.connector.Error as error:

        print("View Freelancer Error:", error)
        return f"Database Error: {error}"

    finally:

        cursor.close()
        conn.close()

    return render_template(
        "view_freelancer.html",
        freelancer=freelancer,
        jobs=jobs
    )


# =====================================================
# HIRE FREELANCER
# =====================================================

@app.route(
    "/hire-freelancer/<int:freelancer_id>",
    methods=["POST"]
)
def hire_freelancer(freelancer_id):

    job_id = request.form.get("job_id", type=int)

    if not job_id:
        flash("Please select a project!", "warning")
        return redirect(
            url_for(
                "view_freelancer",
                freelancer_id=freelancer_id
            )
        )
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role", "").lower() != "client":
        return redirect(url_for("freelancer_dashboard"))

    client_id = session["user_id"]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:

        # 1. Check project belongs to logged-in client
        cursor.execute("""
            SELECT id, title
            FROM jobs
            WHERE id = %s
            AND client_id = %s
        """, (job_id, client_id))

        project = cursor.fetchone()

        if project is None:
            flash("Project not found!", "danger")
            return redirect(url_for("client_dashboard"))

        # 2. Check freelancer exists
        cursor.execute("""
            SELECT id, name, email
            FROM users
            WHERE id = %s
            AND LOWER(TRIM(role)) = 'freelancer'
        """, (freelancer_id,))

        freelancer = cursor.fetchone()

        if freelancer is None:
            flash("Freelancer not found!", "danger")
            return redirect(
                url_for(
                    "project_applications",
                    job_id=job_id
                )
            )

        # 3. Check freelancer has applied for this project
        cursor.execute("""
            SELECT id
            FROM applications
            WHERE job_id = %s
            AND freelancer_id = %s
        """, (job_id, freelancer_id))

        application = cursor.fetchone()

        if application is None:
            flash(
                "This freelancer has not applied for this project!",
                "warning"
            )
            return redirect(
                url_for(
                    "project_applications",
                    job_id=job_id
                )
            )

        # 4. Check if already hired for this project
        cursor.execute("""
            SELECT id
            FROM hired_freelancers
            WHERE client_id = %s
            AND job_id = %s
            AND freelancer_id = %s
        """, (
            client_id,
            job_id,
            freelancer_id
        ))

        already_hired = cursor.fetchone()

        if already_hired:

            flash(
                "This freelancer is already hired for this project!",
                "warning"
            )

        else:

            # 5. Hire freelancer for this specific project
            cursor.execute("""
                INSERT INTO hired_freelancers
                (
                    client_id,
                    job_id,
                    freelancer_id
                )
                VALUES (%s, %s, %s)
            """, (
                client_id,
                job_id,
                freelancer_id
            ))

            conn.commit()

            flash(
                "Freelancer hired successfully!",
                "success"
            )

    except mysql.connector.Error as error:

            conn.rollback()

            print("Hire Freelancer Error:", error)

            flash(
                "Error while hiring freelancer.",
                "danger"
            )

    finally:

            cursor.close()
            conn.close()

    return redirect(
        url_for(
            "project_applications",
            job_id=job_id
        )
    )


# =====================================================
# HIRED FREELANCERS
# =====================================================
@app.route("/hired-freelancers")
def hired_freelancers():

    if "user_id" not in session:
        return redirect(url_for("login"))

    # Only Client can access
    if session.get("role", "").lower() != "client":
        return redirect(url_for("freelancer_dashboard"))

    client_id = session["user_id"]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:

        cursor.execute("""
            SELECT
                h.id,
                h.hired_at,
                h.job_id,
                j.title AS job_title,
                u.id AS freelancer_id,
                u.name,
                u.email,
                u.role
            FROM hired_freelancers h
            LEFT JOIN jobs j ON h.job_id = j.id
            JOIN users u ON h.freelancer_id = u.id
            WHERE h.client_id = %s
            ORDER BY h.id DESC
        """, (client_id,))

        hired = cursor.fetchall()

    except mysql.connector.Error as error:

        print("Hired Freelancer Error:", error)

        hired = []

    finally:

        cursor.close()
        conn.close()

    return render_template(
        "hired_freelancers.html",
        hired=hired
    )

#----------------------Client messages----------------
@app.route("/client-messages")
def client_messages():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role", "").lower() != "client":
        return redirect(url_for("freelancer_dashboard"))

    client_id = session["user_id"]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:

        cursor.execute("""
            SELECT DISTINCT
                u.id AS freelancer_id,
                u.name,
                u.email
            FROM messages m
            JOIN users u
                ON (
                    CASE
                        WHEN m.sender_id = %s
                        THEN m.receiver_id
                        ELSE m.sender_id
                    END
                ) = u.id
            WHERE
                (m.sender_id = %s OR m.receiver_id = %s)
                AND LOWER(TRIM(u.role)) = 'freelancer'
            ORDER BY u.name ASC
        """, (
            client_id,
            client_id,
            client_id
        ))

        freelancers = cursor.fetchall()

    except mysql.connector.Error as error:

        print("Client Messages Error:", error)

        freelancers = []

    finally:

        cursor.close()
        conn.close()

    return render_template(
        "client_messages.html",
        freelancers=freelancers
    )


# =====================================================
# CHAT
# =====================================================

@app.route("/chat/<int:freelancer_id>", methods=["GET", "POST"])
def chat(freelancer_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    current_user_id = session["user_id"]
    current_role = session.get("role", "").lower()

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:

        # =================================================
        # CLIENT IS OPENING CHAT
        # =================================================

        if current_role == "client":

            client_id = current_user_id

            cursor.execute("""
                SELECT
                    id,
                    name,
                    email,
                    role
                FROM users
                WHERE id = %s
                AND LOWER(TRIM(role)) = 'freelancer'
            """, (freelancer_id,))

            other_user = cursor.fetchone()

            if other_user is None:
                return "Freelancer not found", 404

            other_user_id = freelancer_id


        # =================================================
        # FREELANCER IS OPENING CHAT
        # =================================================

        elif current_role == "freelancer":

            freelancer_id = current_user_id

            client_id = request.args.get(
                "client_id",
                type=int
            )

            if not client_id:
                return "Client not selected", 400

            cursor.execute("""
                SELECT
                    id,
                    name,
                    email,
                    role
                FROM users
                WHERE id = %s
                AND LOWER(TRIM(role)) = 'client'
            """, (client_id,))

            other_user = cursor.fetchone()

            if other_user is None:
                return "Client not found", 404

            other_user_id = client_id


        else:

            return redirect(url_for("login"))


        # =================================================
        # SEND MESSAGE
        # =================================================

        if request.method == "POST":

            message = request.form.get(
                "message",
                ""
            ).strip()

            if message:

                cursor.execute("""
                    INSERT INTO messages
                    (
                        sender_id,
                        receiver_id,
                        message
                    )
                    VALUES (%s, %s, %s)
                """, (
                    current_user_id,
                    other_user_id,
                    message
                ))

                conn.commit()

                # =================================================
                # CREATE MESSAGE NOTIFICATION
                # =================================================

                cursor.execute("""
                    SELECT message_notifications
                    FROM users
                    WHERE id = %s
                """, (other_user_id,))

                receiver_settings = cursor.fetchone()

                if (
                    receiver_settings
                    and receiver_settings["message_notifications"]
                ):

                    create_notification(
                        other_user_id,
                        "New Message",
                        f"{session.get('user_name', 'User')}: {message}"
                    )

            if current_role == "client":

                return redirect(
                    url_for(
                        "chat",
                        freelancer_id=other_user_id
                    )
                )

            else:

                return redirect(
                    url_for(
                        "chat",
                        freelancer_id=current_user_id,
                        client_id=other_user_id
                    )
                )

        cursor.execute("""
            UPDATE messages
            SET is_read = TRUE
            WHERE sender_id = %s
            AND receiver_id = %s
            AND is_read = FALSE
        """, (
            other_user_id,
            current_user_id
        ))

        conn.commit()


        # =================================================
        # GET MESSAGES
        # =================================================

        cursor.execute("""
            SELECT
                m.id,
                m.sender_id,
                m.receiver_id,
                m.message,
                m.sent_at,
                u.name AS sender_name

            FROM messages m

            JOIN users u
                ON m.sender_id = u.id

            WHERE
                (
                    m.sender_id = %s
                    AND m.receiver_id = %s
                )
                OR
                (
                    m.sender_id = %s
                    AND m.receiver_id = %s
                )

            ORDER BY m.sent_at ASC
        """, (
            client_id,
            freelancer_id,
            freelancer_id,
            client_id
        ))

        messages = cursor.fetchall()

    except mysql.connector.Error as error:

        print("Chat Database Error:", error)

        return f"Database Error: {error}"

    finally:

        cursor.close()
        conn.close()


    return render_template(
        "chat.html",
        freelancer=other_user,
        messages=messages
    )
#----------------freelancer dashboard----------------
@app.route("/freelancer-dashboard")
def freelancer_dashboard():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role", "").lower() != "freelancer":
        return redirect(url_for("client_dashboard"))

    freelancer_id = session["user_id"]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    notifications = []
    unread_messages = 0
    projects = []
    applications_count = 0
    hired_projects = 0
    active_projects = 0
    total_earnings = 0

    settings = {
        "show_dashboard_statistics": True,
        "show_recent_activity": True
    }

    try:
        cursor.execute("""
            SELECT
                show_dashboard_statistics,
                show_recent_activity
            FROM users
            WHERE id = %s
        """, (freelancer_id,))

        settings = cursor.fetchone()

        # ================= NOTIFICATIONS =================

        cursor.execute("""
            SELECT
                id,
                title,
                message,
                is_read,
                created_at
            FROM notifications
            WHERE user_id = %s
            ORDER BY created_at DESC
        """, (freelancer_id,))

        notifications = cursor.fetchall()

        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM messages
            WHERE receiver_id = %s
            AND is_read = FALSE
        """, (freelancer_id,))

        unread_messages = cursor.fetchone()["total"]




        # ================= RECENT PROJECTS =================

        cursor.execute("""
            SELECT
                j.id,
                j.title,
                j.description,
                j.skills,
                j.budget,
                j.deadline,
                j.created_at,
                u.name AS client_name
            FROM jobs j
            JOIN users u
                ON j.client_id = u.id
            ORDER BY j.created_at DESC
            LIMIT 5
        """)

        projects = cursor.fetchall()
        # ================= FREELANCER STATS =================

        # Total Applications
        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM applications
            WHERE freelancer_id = %s
        """, (freelancer_id,))

        applications_count = cursor.fetchone()["total"]

        # Hired Projects
        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM hired_freelancers
            WHERE freelancer_id = %s
        """, (freelancer_id,))

        hired_projects = cursor.fetchone()["total"]

        # Active Projects
        # Currently hired projects are considered active
        active_projects = hired_projects

        # Total Earnings
        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM payments
            WHERE freelancer_id = %s
            AND LOWER(status) IN ('paid', 'completed')
        """, (freelancer_id,))

        total_earnings = cursor.fetchone()["total"]


    except mysql.connector.Error as error:

        print("Freelancer Dashboard Error:", error)

        notifications = []
        projects = []
        applications_count = 0
        hired_projects = 0
        active_projects = 0
        total_earnings = 0


    finally:

        cursor.close()
        conn.close()

    return render_template(
        "freelancer_dashboard.html",
        user_name=session.get("user_name", "Freelancer"),
        notifications=notifications,
        projects=projects,
        applications_count=applications_count,
        hired_projects=hired_projects,
        active_projects=active_projects,
        total_earnings=total_earnings,
        unread_messages=unread_messages,
        settings=settings
    )
@app.route("/unread-message-count")
def unread_message_count():

    if "user_id" not in session:
        return {"count": 0}

    if session.get("role", "").lower() != "freelancer":
        return {"count": 0}

    freelancer_id = session["user_id"]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:

        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM messages
            WHERE receiver_id = %s
            AND is_read = FALSE
        """, (freelancer_id,))

        result = cursor.fetchone()

        return {
            "count": result["total"]
        }

    except mysql.connector.Error as error:

        print("Unread Message Count Error:", error)

        return {"count": 0}

    finally:

        cursor.close()
        conn.close()
#---------------find projects------------------------
@app.route("/find-projects")
def find_projects():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role", "").lower() != "freelancer":
        return redirect(url_for("client_dashboard"))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT
                j.id,
                j.title,
                j.description,
                j.skills,
                j.budget,
                j.deadline,
                j.created_at,
                u.name AS client_name
            FROM jobs j
            JOIN users u ON j.client_id = u.id
            ORDER BY j.created_at DESC
        """)

        projects = cursor.fetchall()

    except mysql.connector.Error as error:
        print("Find Projects Error:", error)
        projects = []

    finally:
        cursor.close()
        conn.close()

    return render_template(
        "find_projects.html",
        projects=projects,
        user_name=session.get("user_name", "Freelancer")
    )

# ================= HIRED PROJECTS =================

@app.route("/hired-projects")
def hired_projects():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role", "").lower() != "freelancer":
        return redirect(url_for("client_dashboard"))

    freelancer_id = session["user_id"]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    hired_projects = []

    try:

        cursor.execute("""
            SELECT
                h.id,
                h.job_id,
                h.hired_at,
                j.title,
                j.description,
                j.skills,
                j.budget,
                j.deadline,
                u.name AS client_name,
                u.email AS client_email
            FROM hired_freelancers h

            JOIN jobs j
                ON h.job_id = j.id

            JOIN users u
                ON h.client_id = u.id

            WHERE h.freelancer_id = %s

            ORDER BY h.hired_at DESC
        """, (freelancer_id,))

        hired_projects = cursor.fetchall()

    except mysql.connector.Error as error:

        print("Hired Projects Error:", error)

        hired_projects = []

    finally:

        cursor.close()
        conn.close()

    return render_template(
        "hired_projects.html",
        hired_projects=hired_projects,
        user_name=session.get("user_name", "Freelancer")
    )

#------------------apply--------------
@app.route("/apply-project/<int:job_id>", methods=["GET", "POST"])
def apply_project(job_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role", "").lower() != "freelancer":
        return redirect(url_for("client_dashboard"))

    freelancer_id = session["user_id"]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT id, title, description, skills, budget, deadline
            FROM jobs
            WHERE id = %s
        """, (job_id,))

        project = cursor.fetchone()

        if project is None:
            return "Project not found", 404

        if request.method == "POST":

            proposal = request.form.get("proposal", "").strip()

            if not proposal:
                return render_template(
                    "apply_project.html",
                    project=project,
                    error="Please enter your proposal."
                )

            cursor.execute("""
                SELECT id
                FROM applications
                WHERE job_id = %s AND freelancer_id = %s
            """, (job_id, freelancer_id))

            existing = cursor.fetchone()

            if existing:
                return render_template(
                    "apply_project.html",
                    project=project,
                    error="You have already applied for this project."
                )

            cursor.execute("""
                INSERT INTO applications
                (job_id, freelancer_id, proposal)
                VALUES (%s, %s, %s)
            """, (job_id, freelancer_id, proposal))

            conn.commit()

            return redirect(url_for("my_applications"))

    except mysql.connector.Error as error:
        print("Apply Project Error:", error)
        return f"Database Error: {error}"

    finally:
        cursor.close()
        conn.close()

    return render_template(
        "apply_project.html",
        project=project
    )

#-------------------------application---------------
@app.route("/my-applications")
def my_applications():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role", "").lower() != "freelancer":
        return redirect(url_for("client_dashboard"))

    freelancer_id = session["user_id"]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:

        cursor.execute("""
            SELECT
                a.id,
                a.job_id,
                a.freelancer_id,
                a.proposal,
                a.status,
                a.applied_at,

                j.title,
                j.description,
                j.skills,
                j.budget,
                j.deadline,

                u.name AS client_name

            FROM applications a

            JOIN jobs j
                ON a.job_id = j.id

            JOIN users u
                ON j.client_id = u.id

            WHERE a.freelancer_id = %s

            ORDER BY a.applied_at DESC

        """, (freelancer_id,))

        applications = cursor.fetchall()

        print("Freelancer ID:", freelancer_id)
        print("Applications:", applications)

    except mysql.connector.Error as error:

        print("Applications Error:", error)

        applications = []

    finally:

        cursor.close()
        conn.close()

    return render_template(
        "my_applications.html",
        applications=applications,
        user_name=session.get("user_name", "Freelancer")
    )

#---------------client application-----------------
@app.route("/project-applications/<int:job_id>")
def project_applications(job_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role", "").lower() != "client":
        return redirect(url_for("freelancer_dashboard"))

    client_id = session["user_id"]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT id, title
            FROM jobs
            WHERE id = %s AND client_id = %s
        """, (job_id, client_id))

        project = cursor.fetchone()

        if project is None:
            return "Project not found", 404

        cursor.execute("""
            SELECT
                a.id,
                a.proposal,
                a.applied_at,
                u.id AS freelancer_id,
                u.name AS freelancer_name,
                u.email AS freelancer_email,
                u.skills AS freelancer_skills,
                u.resume_file AS freelancer_resume
            FROM applications a
            JOIN users u ON a.freelancer_id = u.id
            WHERE a.job_id = %s
            ORDER BY a.applied_at DESC
        """, (job_id,))

        applications = cursor.fetchall()

    except mysql.connector.Error as error:
        print("Applications Error:", error)
        return f"Database Error: {error}"

    finally:
        cursor.close()
        conn.close()

    return render_template(
        "project_applications.html",
        project=project,
        applications=applications
    )

#---------------------setting--------------------------

@app.route("/client-settings", methods=["GET", "POST"])
def client_settings():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role", "").lower() != "client":
        return redirect(url_for("freelancer_dashboard"))

    client_id = session["user_id"]

    if request.method == "POST":

        current_password = request.form.get(
            "current_password",
            ""
        ).strip()

        new_password = request.form.get(
            "new_password",
            ""
        ).strip()

        confirm_password = request.form.get(
            "confirm_password",
            ""
        ).strip()

        # Check new passwords
        if new_password != confirm_password:

            flash(
                "New passwords do not match!",
                "danger"
            )

            return redirect(
                url_for("client_settings")
            )

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:

            # Get current password
            cursor.execute("""
                SELECT password
                FROM users
                WHERE id = %s
            """, (client_id,))

            user = cursor.fetchone()

            if user is None:

                flash(
                    "User not found!",
                    "danger"
                )

                return redirect(
                    url_for("client_settings")
                )

            # Check current password
            if user["password"] != current_password:

                flash(
                    "Current password is incorrect!",
                    "danger"
                )

                return redirect(
                    url_for("client_settings")
                )

            # Update password
            cursor.execute("""
                UPDATE users
                SET password = %s
                WHERE id = %s
            """, (
                new_password,
                client_id
            ))

            conn.commit()

            flash(
                "Password changed successfully!",
                "success"
            )

        except mysql.connector.Error as error:

            conn.rollback()

            print(
                "Client Settings Error:",
                error
            )

            flash(
                "Error while changing password.",
                "danger"
            )

        finally:

            cursor.close()
            conn.close()

        return redirect(
            url_for("client_settings")
        )

    # Get client information for settings page
    cursor = None
    conn = get_db_connection()

    try:
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
           SELECT
    name,
    email,
    role,
    show_dashboard_statistics,
    show_recent_activity,
    project_notifications,
    message_notifications
FROM users
WHERE id = %s
        """, (client_id,))

        user = cursor.fetchone()

    finally:
        if cursor:
            cursor.close()
        conn.close()

    return render_template(
        "client_settings.html",
        user=user
    )


#-------------------client preferences-------------
@app.route("/client-preferences", methods=["POST"])
def client_preferences():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role", "").lower() != "client":
        return redirect(url_for("freelancer_dashboard"))

    client_id = session["user_id"]

    # Checkbox checked असेल तर "on" मिळते
    show_dashboard_statistics = (
        1 if request.form.get("show_dashboard_statistics") == "on" else 0
    )

    show_recent_activity = (
        1 if request.form.get("show_recent_activity") == "on" else 0
    )

    conn = get_db_connection()
    cursor = conn.cursor()

    try:

        cursor.execute("""
            UPDATE users
            SET
                show_dashboard_statistics = %s,
                show_recent_activity = %s
            WHERE id = %s
        """, (
            show_dashboard_statistics,
            show_recent_activity,
            client_id
        ))

        conn.commit()

        flash(
            "Account preferences saved successfully!",
            "success"
        )

    except mysql.connector.Error as error:

        conn.rollback()

        print(
            "Client Preferences Error:",
            error
        )

        flash(
            "Error while saving account preferences.",
            "danger"
        )

    finally:

        cursor.close()
        conn.close()

    return redirect(url_for("client_settings"))

#--------------------notification---------------
@app.route("/client-notifications", methods=["POST"])
def client_notifications():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role", "").lower() != "client":
        return redirect(url_for("freelancer_dashboard"))

    client_id = session["user_id"]

    project_notifications = (
        1 if request.form.get("project_notifications") == "on" else 0
    )

    message_notifications = (
        1 if request.form.get("message_notifications") == "on" else 0
    )

    conn = get_db_connection()
    cursor = conn.cursor()

    try:

        cursor.execute("""
            UPDATE users
            SET
                project_notifications = %s,
                message_notifications = %s
            WHERE id = %s
        """, (
            project_notifications,
            message_notifications,
            client_id
        ))

        conn.commit()

        flash(
            "Notification settings saved successfully!",
            "success"
        )

    except mysql.connector.Error as error:

        conn.rollback()

        print("Client Notification Error:", error)

        flash(
            "Error while saving notification settings.",
            "danger"
        )

    finally:

        cursor.close()
        conn.close()

    return redirect(url_for("client_settings"))

#----------------------------read notification--------------

@app.route("/read-notification/<int:notification_id>")
def read_notification(notification_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role", "").lower() != "freelancer":
        return redirect(url_for("client_dashboard"))

    freelancer_id = session["user_id"]

    conn = get_db_connection()
    cursor = conn.cursor()

    try:

        cursor.execute("""
            UPDATE notifications
            SET is_read = 1
            WHERE id = %s
            AND user_id = %s
        """, (
            notification_id,
            freelancer_id
        ))

        conn.commit()

    except mysql.connector.Error as error:

        conn.rollback()
        print("Read Notification Error:", error)

    finally:

        cursor.close()
        conn.close()

    return redirect(url_for("freelancer_dashboard"))


# =====================================================
# PAYMENTS
# =====================================================

@app.route('/client/payment/<int:project_id>', methods=['GET', 'POST'])
def client_payment(project_id):

    # User login आहे का?
    if 'user_id' not in session:
        flash('Please login first.', 'warning')
        return redirect(url_for('login'))

    client_id = session['user_id']

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # फक्त स्वतःच्या project साठी payment करता येईल
        cursor.execute("""
            SELECT id, title, budget, client_id
            FROM jobs
            WHERE id = %s AND client_id = %s
        """, (project_id, client_id))

        project = cursor.fetchone()

        if not project:
            flash('Project not found or access denied.', 'danger')
            return redirect(url_for('projects_page'))

        if request.method == 'POST':
            freelancer_id = request.form.get('freelancer_id', type=int)
            outcome = request.form.get('outcome')

            if not freelancer_id or freelancer_id <= 0:
                flash('Enter a valid freelancer ID.', 'danger')
                return redirect(
                    url_for('client_payment', project_id=project_id)
                )

            if outcome not in ('success', 'fail'):
                flash('Select a valid demo payment outcome.', 'danger')
                return redirect(
                    url_for('client_payment', project_id=project_id)
                )

            try:
                amount = Decimal(str(project['budget']))
                if not amount.is_finite() or amount <= 0:
                    raise InvalidOperation
            except (InvalidOperation, TypeError, ValueError):
                flash('This project has an invalid budget.', 'danger')
                return redirect(
                    url_for('client_payment', project_id=project_id)
                )

            transaction_id = 'DEMO-' + secrets.token_hex(8).upper()
            status = 'Paid' if outcome == 'success' else 'Failed'

            cursor.execute("""
                INSERT INTO payments
                (transaction_id, project_id, client_id, freelancer_id,
                 amount, payment_method, status)
                VALUES (%s, %s, %s, %s, %s, 'DEMO', %s)
            """, (
                transaction_id,
                project_id,
                client_id,
                freelancer_id,
                amount,
                status
            ))

            conn.commit()

            flash(
                f'Demo payment {status.lower()}! '
                f'Transaction ID: {transaction_id}',
                'success' if status == 'Paid' else 'danger'
            )

            return redirect(
                url_for('client_payment', project_id=project_id)
            )

        return render_template(
            'payment.html',
            project=project
        )

    except Exception:
        conn.rollback()
        app.logger.exception('Demo payment error')
        flash('Unable to process demo payment. Check the server log.', 'danger')
        return redirect(url_for('projects_page'))

    finally:
        cursor.close()
        conn.close()
# =====================================================
# MAKE PAYMENT
# =====================================================
@app.route("/make-payment/<int:job_id>/<int:freelancer_id>", methods=["GET", "POST"])
def make_payment(job_id, freelancer_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    # Only Client can make payment
    if session.get("role", "").lower() != "client":
        return redirect(url_for("freelancer_dashboard"))

    client_id = session["user_id"]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # Check hired freelancer and project
        cursor.execute("""
            SELECT
                h.id,
                h.job_id,
                h.freelancer_id,
                j.title AS job_title,
                u.name AS freelancer_name,
                u.upi_id AS freelancer_upi_id
            FROM hired_freelancers h
            JOIN jobs j ON h.job_id = j.id
            JOIN users u ON h.freelancer_id = u.id
            WHERE h.client_id = %s
              AND h.job_id = %s
              AND h.freelancer_id = %s
        """, (client_id, job_id, freelancer_id))

        hired = cursor.fetchone()

        if hired is None:
            return "Hired freelancer/project not found", 404

        # Validate amount before displaying the UPI QR page
        if request.method == "POST":
            from decimal import Decimal, InvalidOperation

            try:
                amount = Decimal(
                    request.form.get("amount", "").strip()
                )

                if (
                    not amount.is_finite()
                    or amount <= 0
                    or amount.as_tuple().exponent < -2
                    or amount > Decimal("10000000")
                ):
                    raise InvalidOperation

            except (InvalidOperation, ValueError):
                return render_template(
                    "make_payment.html",
                    hired=hired,
                    error="Please enter a valid amount with up to 2 decimal places."
                )

            # This route only prepares the payment page.
            # It does not mark a payment as completed.
            return render_template(
                "make_payment.html",
                hired=hired,
                amount=float(amount)
            )

        return render_template(
            "make_payment.html",
            hired=hired
        )

    except mysql.connector.Error:
        app.logger.exception("Make Payment database error")
        return "Database error while opening payment page.", 500

    finally:
        cursor.close()
        conn.close()


# =====================================================
# FREELANCER MESSAGES
# =====================================================

@app.route("/freelancer-messages")
def freelancer_messages():

    if "user_id" not in session:
        return redirect(url_for("login"))

    # Only Freelancer can access
    if session.get("role", "").lower() != "freelancer":
        return redirect(url_for("client_dashboard"))

    freelancer_id = session["user_id"]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT DISTINCT
                u.id AS client_id,
                u.name,
                u.email
            FROM messages m
            JOIN users u
                ON (
                    CASE
                        WHEN m.sender_id = %s
                        THEN m.receiver_id
                        ELSE m.sender_id
                    END
                ) = u.id
            WHERE
                (m.sender_id = %s OR m.receiver_id = %s)
                AND LOWER(TRIM(u.role)) = 'client'
            ORDER BY u.name ASC
        """, (
            freelancer_id,
            freelancer_id,
            freelancer_id
        ))

        clients = cursor.fetchall()

    except mysql.connector.Error:
        app.logger.exception("Freelancer Messages database error")
        clients = []

    finally:
        cursor.close()
        conn.close()

    return render_template(
        "freelancer_messages.html",
        clients=clients
    )



@app.route(
    "/record-payment-intent/<int:job_id>/<int:freelancer_id>",
    methods=["POST"]
)
def record_payment_intent(job_id, freelancer_id):
    from decimal import Decimal, InvalidOperation

    if "user_id" not in session:
        return {"success": False, "message": "Please login first."}, 401

    if session.get("role", "").lower() != "client":
        return {"success": False, "message": "Only clients can make payments."}, 403

    data = request.get_json(silent=True) or {}

    try:
        amount = Decimal(str(data.get("amount", "")).strip())

        if (
            not amount.is_finite()
            or amount <= 0
            or amount.as_tuple().exponent < -2
            or amount > Decimal("10000000")
        ):
            raise InvalidOperation

    except (InvalidOperation, ValueError):
        return {
            "success": False,
            "message": "Enter a valid amount."
        }, 400

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        client_id = session["user_id"]

        cursor.execute("""
            SELECT id
            FROM hired_freelancers
            WHERE client_id = %s
              AND job_id = %s
              AND freelancer_id = %s
        """, (client_id, job_id, freelancer_id))

        if cursor.fetchone() is None:
            return {
                "success": False,
                "message": "Hired project not found."
            }, 404

        cursor.execute("""
            INSERT INTO payments (
                client_id, freelancer_id, job_id, amount, status
            )
            VALUES (%s, %s, %s, %s, %s)
        """, (
            client_id,
            freelancer_id,
            job_id,
            amount,
            "Pending"
        ))

        conn.commit()

        return {
            "success": True,
            "message": "Payment record saved as Pending."
        }

    except mysql.connector.Error:
        conn.rollback()
        app.logger.exception("Payment record error")

        return {
            "success": False,
            "message": "Unable to save payment record."
        }, 500

    finally:
        cursor.close()
        conn.close()



#-------------------reports---------------------
@app.route("/reports")
def reports():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role", "").lower() != "client":
        return redirect(url_for("freelancer_dashboard"))

    client_id = session["user_id"]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    total_projects = 0
    total_payments = 0
    total_amount = 0
    payments = []

    try:

        # ==============================
        # TOTAL PROJECTS
        # ==============================

        cursor.execute("""
            SELECT COUNT(*) AS total_projects
            FROM jobs
            WHERE client_id = %s
        """, (client_id,))

        total_projects = cursor.fetchone()["total_projects"]


        # ==============================
        # TOTAL PAYMENTS + AMOUNT
        # ==============================

        cursor.execute("""
            SELECT
                COUNT(*) AS total_payments,
                COALESCE(SUM(amount), 0) AS total_amount
            FROM payments
            WHERE client_id = %s
        """, (client_id,))

        payment_data = cursor.fetchone()

        total_payments = payment_data["total_payments"]
        total_amount = payment_data["total_amount"]


        # ==============================
        # PAYMENT HISTORY
        # ==============================

        cursor.execute("""
            SELECT
                p.payment_id,
                p.job_id,
                p.freelancer_id,
                p.amount,
                p.status,
                p.payment_date,
                j.title AS job_title,
                u.name AS freelancer_name
            FROM payments p
            JOIN jobs j ON p.job_id = j.id
            JOIN users u ON p.freelancer_id = u.id
            WHERE p.client_id = %s
            ORDER BY p.payment_id DESC
        """, (client_id,))

        payments = cursor.fetchall()


        print("REPORT PAYMENT DATA:", payments)


    except mysql.connector.Error as error:

        print("Reports Error:", error)

        payments = []


    finally:

        cursor.close()
        conn.close()


    return render_template(
        "reports.html",
        total_projects=total_projects,
        total_payments=total_payments,
        total_amount=total_amount,
        payments=payments
    )



@app.route("/freelancer-profile", methods=["GET", "POST"])
def freelancer_profile():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role", "").lower() != "freelancer":
        return redirect(url_for("client_dashboard"))

    freelancer_id = session["user_id"]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    message = None
    error = None

    try:
        if request.method == "POST":
            upi_id = request.form.get("upi_id", "").strip()

            # Basic UPI ID validation
            if not upi_id or len(upi_id) > 100 or upi_id.count("@") != 1:
                error = "Please enter a valid UPI ID."
            else:
                parts = upi_id.split("@")

                if (
                    not parts[0]
                    or not parts[1]
                    or any(ch.isspace() for ch in upi_id)
                ):
                    error = "Please enter a valid UPI ID."
                else:
                    cursor.execute("""
                        UPDATE users
                        SET upi_id = %s
                        WHERE id = %s
                    """, (upi_id, freelancer_id))

                    conn.commit()
                    message = "UPI ID saved successfully."

        cursor.execute("""
            SELECT
                id,
                name,
                email,
                skills,
                created_at,
                upi_id
            FROM users
            WHERE id = %s
        """, (freelancer_id,))

        freelancer = cursor.fetchone()

    except mysql.connector.Error:
        conn.rollback()
        app.logger.exception("Freelancer Profile Error")
        freelancer = None
        error = "Unable to save or load your profile. Please try again."

    finally:
        cursor.close()
        conn.close()

    return render_template(
        "freelancer_profile.html",
        freelancer=freelancer,
        message=message,
        error=error
    )





@app.route("/edit-freelancer-profile", methods=["GET", "POST"])
def edit_freelancer_profile():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role", "").lower() != "freelancer":
        return redirect(url_for("client_dashboard"))

    freelancer_id = session["user_id"]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:

        if request.method == "POST":

            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip()
            skills = request.form.get("skills", "").strip()

            cursor.execute("""
                UPDATE users
                SET name = %s,
                    email = %s,
                    skills = %s
                WHERE id = %s
            """, (
                name,
                email,
                skills,
                freelancer_id
            ))

            conn.commit()

            session["user_name"] = name

            return redirect(url_for("edit_freelancer_profile"))


        cursor.execute("""
            SELECT
                id,
                name,
                email,
                skills,
                resume_file
            FROM users
            WHERE id = %s
        """, (freelancer_id,))

        freelancer = cursor.fetchone()

    except mysql.connector.Error as error:

        print("Edit Freelancer Profile Error:", error)

        freelancer = None

    finally:

        cursor.close()
        conn.close()

    return render_template(
        "edit_freelancer_profile.html",
        freelancer=freelancer
    )

@app.route("/freelancer-settings", methods=["GET", "POST"])
def freelancer_settings():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role", "").lower() != "freelancer":
        return redirect(url_for("client_dashboard"))

    freelancer_id = session["user_id"]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:

        if request.method == "POST":

            setting = request.form.get("setting")

            if setting == "project_notifications":
                value = 1 if request.form.get("project_notifications") else 0

                cursor.execute("""
                    UPDATE users
                    SET project_notifications = %s
                    WHERE id = %s
                """, (value, freelancer_id))

            elif setting == "message_notifications":
                value = 1 if request.form.get("message_notifications") else 0

                cursor.execute("""
                    UPDATE users
                    SET message_notifications = %s
                    WHERE id = %s
                """, (value, freelancer_id))

            elif setting == "show_dashboard_statistics":
                value = 1 if request.form.get("show_dashboard_statistics") else 0

                cursor.execute("""
                    UPDATE users
                    SET show_dashboard_statistics = %s
                    WHERE id = %s
                """, (value, freelancer_id))

            elif setting == "show_recent_activity":
                value = 1 if request.form.get("show_recent_activity") else 0

                cursor.execute("""
                        UPDATE users
                        SET show_recent_activity = %s
                        WHERE id = %s
                    """, (value, freelancer_id))

            conn.commit()

        cursor.execute("""
            SELECT
                project_notifications,
                message_notifications,
                show_dashboard_statistics,
                show_recent_activity
            FROM users
            WHERE id = %s
        """, (freelancer_id,))

        settings = cursor.fetchone()

    except mysql.connector.Error as error:

        print("Freelancer Settings Error:", error)

        settings = {
            "project_notifications": True,
            "message_notifications": True,
            "show_dashboard_statistics": True,
            "show_recent_activity": True
        }

    finally:

        cursor.close()
        conn.close()

    return render_template(
        "freelancer_settings.html",
        settings=settings
    )


@app.route("/change-password", methods=["GET", "POST"])
def change_password():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role", "").lower() != "freelancer":
        return redirect(url_for("client_dashboard"))

    freelancer_id = session["user_id"]

    if request.method == "POST":

        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        # Check new password and confirm password
        if new_password != confirm_password:
            return render_template(
                "change_password.html",
                error="New password and confirm password do not match."
            )

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            # Get current password
            cursor.execute("""
                SELECT password
                FROM users
                WHERE id = %s
            """, (freelancer_id,))

            user = cursor.fetchone()

            # Check current password
            if not user or user["password"] != current_password:
                return render_template(
                    "change_password.html",
                    error="Current password is incorrect."
                )

            # Update password
            cursor.execute("""
                UPDATE users
                SET password = %s
                WHERE id = %s
            """, (new_password, freelancer_id))

            conn.commit()

            return render_template(
                "change_password.html",
                success="Password changed successfully."
            )

        except mysql.connector.Error as error:
            print("Change Password Error:", error)

            return render_template(
                "change_password.html",
                error="Something went wrong. Please try again."
            )

        finally:
            cursor.close()
            conn.close()

    return render_template("change_password.html")


# =====================================================
# RESUME: FREELANCER UPLOAD
# =====================================================

@app.route("/upload-resume", methods=["POST"])
def upload_resume():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role", "").lower() != "freelancer":
        return redirect(url_for("client_dashboard"))

    file = request.files.get("resume")

    if not file or file.filename == "":
        flash("Please choose a resume file.", "danger")
        return redirect(url_for("edit_freelancer_profile"))

    if not allowed_resume(file.filename):
        flash("Only PDF files are allowed.", "danger")
        return redirect(url_for("edit_freelancer_profile"))

    # Unique, safe file name
    filename = f"{session['user_id']}_{uuid.uuid4().hex}.pdf"
    file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # Remove the old resume file (if any)
        cursor.execute(
            "SELECT resume_file FROM users WHERE id = %s",
            (session["user_id"],)
        )
        old = cursor.fetchone()

        if old and old["resume_file"]:
            old_path = os.path.join(app.config["UPLOAD_FOLDER"], old["resume_file"])
            if os.path.exists(old_path):
                os.remove(old_path)

        cursor.execute(
            "UPDATE users SET resume_file = %s WHERE id = %s",
            (filename, session["user_id"])
        )
        conn.commit()

        flash("Resume uploaded successfully!", "success")

    except mysql.connector.Error as error:
        conn.rollback()
        print("Upload Resume Error:", error)
        flash("Error while uploading resume.", "danger")

    finally:
        cursor.close()
        conn.close()

        flash("Resume uploaded successfully!", "success")

    return redirect(url_for("edit_freelancer_profile"))


# =====================================================
# RESUME: CLIENT VIEW
# =====================================================

@app.route("/resume/<int:freelancer_id>")
def view_resume(freelancer_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    # Only the owner or a client can open a resume
    if (session["user_id"] != freelancer_id
            and session.get("role", "").lower() != "client"):
        abort(403)

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute(
            "SELECT resume_file FROM users WHERE id = %s",
            (freelancer_id,)
        )
        row = cursor.fetchone()

    finally:
        cursor.close()
        conn.close()

    if not row or not row["resume_file"]:
        abort(404)

    return send_from_directory(app.config["UPLOAD_FOLDER"], row["resume_file"])


@app.errorhandler(413)
def file_too_large(error):
    flash("File too large. Maximum size is 2 MB.", "danger")
    return redirect(request.referrer or url_for("home"))



@app.route("/find-work")
def find_work_page():
    search = request.args.get("search", "").strip()

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        if search:
            keyword = f"%{search}%"
            cursor.execute("""
                SELECT id, title, description, skills, budget, deadline
                FROM jobs
                WHERE title LIKE %s
                   OR description LIKE %s
                   OR skills LIKE %s
                ORDER BY id DESC
            """, (keyword, keyword, keyword))
        else:
            cursor.execute("""
                SELECT id, title, description, skills, budget, deadline
                FROM jobs
                ORDER BY id DESC
            """)

        jobs = cursor.fetchall()

    except mysql.connector.Error as error:
        print("Find Work Error:", error)
        jobs = []

    finally:
        cursor.close()
        conn.close()

    return render_template("find_work.html", jobs=jobs)



@app.route("/find-talent")
def find_talent_page():
    search = request.args.get("search", "").strip()

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        if search:
            keyword = f"%{search}%"
            cursor.execute("""
                SELECT id, name, skills
                FROM users
                WHERE LOWER(TRIM(role)) = 'freelancer'
                  AND (name LIKE %s OR skills LIKE %s)
                ORDER BY id DESC
            """, (keyword, keyword))
        else:
            cursor.execute("""
                SELECT id, name, skills
                FROM users
                WHERE LOWER(TRIM(role)) = 'freelancer'
                ORDER BY id DESC
            """)

        freelancers = cursor.fetchall()

    except mysql.connector.Error as error:
        print("Find Talent Error:", error)
        freelancers = []

    finally:
        cursor.close()
        conn.close()

    return render_template(
        "find_talent.html",
        freelancers=freelancers
    )



@app.route("/projects")
def projects_page():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT id, title, description, skills, budget, deadline
            FROM jobs
            ORDER BY id DESC
        """)
        projects = cursor.fetchall()

    except mysql.connector.Error as error:
        print("Projects Page Error:", error)
        projects = []

    finally:
        cursor.close()
        conn.close()

    return render_template("projects_page.html", projects=projects)


@app.route("/how-it-works")
def how_it_works_page():
    return render_template("how_it_works.html")



@app.route("/pricing")
def pricing_page():
    return render_template("pricing.html")




@app.route("/payments")
def payments():
    if "user_id" not in session:
        return redirect(url_for("login"))

    client_id = session["user_id"]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT
                p.payment_id,
                p.job_id,
                p.freelancer_id,
                p.amount,
                p.status,
                p.payment_date,
                j.title AS job_title,
                u.name AS freelancer_name
            FROM payments p
            JOIN jobs j ON p.job_id = j.id
            JOIN users u ON p.freelancer_id = u.id
            WHERE p.client_id = %s
            ORDER BY p.payment_id DESC
        """, (client_id,))

        payment_list = cursor.fetchall()

        return render_template(
            "payment.html",
            payments=payment_list
        )

    except mysql.connector.Error as error:
        print("Payments Page Error:", error)
        return f"Database Error: {error}"

    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":

    app.run(
        debug=True
    )
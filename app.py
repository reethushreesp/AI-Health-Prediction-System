
import os
import re
import math
from datetime import datetime, date

from flask import (
    Flask, render_template, request,
    redirect, url_for, flash
)
from dotenv import load_dotenv
from google import genai
from markupsafe import escape

from database import get_connection, create_table


# -----------------------------------
# Flask and Gemini configuration
# -----------------------------------

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "local-development-key"
)

api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key) if api_key else None

create_table()


# -----------------------------------
# Patient data validation
# -----------------------------------

def validate_patient_data(form):

    errors = []

    full_name = form.get("full_name", "").strip()
    dob = form.get("dob", "").strip()
    email = form.get("email", "").strip()

    if not full_name:
        errors.append("Full name is required.")

    try:
        birth_date = datetime.strptime(
            dob, "%d/%m/%Y"
        ).date()

        if birth_date.strftime("%d/%m/%Y") != dob:
            errors.append(
                "Date must be in DD/MM/YYYY format."
            )

        elif birth_date > date.today():
            errors.append(
                "Date of birth cannot be in the future."
            )

    except ValueError:
        errors.append(
            "Please enter a valid date in DD/MM/YYYY format."
        )

    if not re.fullmatch(
        r"[^@\s]+@[^@\s]+\.[^@\s]+",
        email
    ):
        errors.append(
            "Please enter a valid email address."
        )

    try:
        glucose = float(form.get("glucose", ""))
        haemoglobin = float(form.get("haemoglobin", ""))
        cholesterol = float(form.get("cholesterol", ""))

        values = (glucose, haemoglobin, cholesterol)

        if not all(
            math.isfinite(value) and value > 0
            for value in values
        ):
            errors.append(
                "Blood test values must be positive numbers."
            )

    except (ValueError, TypeError):
        errors.append(
            "Blood test values must be numeric."
        )
        glucose = haemoglobin = cholesterol = 0

    if errors:
        return None, " ".join(errors)

    patient = {
        "full_name": full_name,
        "dob": dob,
        "email": email,
        "glucose": glucose,
        "haemoglobin": haemoglobin,
        "cholesterol": cholesterol
    }

    return patient, None


# -----------------------------------
# Gemini AI health remarks
# -----------------------------------

def generate_ai_remarks(glucose, haemoglobin, cholesterol):

    if not client:
        return (
            "<b>AI Remarks:</b> API key is not configured."
        )

    prompt = f"""
You are an educational health information assistant.

Review these values:
Glucose: {glucose}
Haemoglobin: {haemoglobin}
Cholesterol: {cholesterol}

Write a short report using these exact headings:

**Glucose:** One short observation.
**Haemoglobin:** One short observation.
**Cholesterol:** One short observation.
**Overall:** One short recommendation.

Instructions:
- Use simple and professional language.
- Keep the complete response under 80 words.
- Do not diagnose diseases or prescribe treatment.
- Do not assume units or reference ranges.
- Do not call values normal, high or low without sufficient context.
- Recommend professional review.
- Return only the report.
"""

    models = [
        "gemini-3.8-flash",
        "gemini-3.5-flash-lite"
    ]

    for model_name in models:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt
            )

            if response.text:
                # Escape model output before displaying it.
                safe_text = str(escape(response.text.strip()))

                # Bold only the four approved headings.
                safe_text = re.sub(
                    r"\*\*(Glucose|Haemoglobin|Cholesterol|Overall):\*\*",
                    r"<strong>\1:</strong>",
                    safe_text
                )

                return safe_text.replace("\n", "<br>")

        except Exception as error:
            print(
                f"Gemini error with {model_name}: {error}",
                flush=True
            )

    return (
        "<b>AI Remarks:</b> AI service is temporarily "
        "unavailable. Please try again later."
    )


# -----------------------------------
# Home page - Display patient records
# -----------------------------------

@app.route("/")
def index():

    connection = get_connection()

    patients = connection.execute(
        "SELECT * FROM patients ORDER BY id DESC"
    ).fetchall()

    connection.close()

    return render_template(
        "index.html",
        patients=patients
    )


# -----------------------------------
# Add patient record
# -----------------------------------

@app.route("/add", methods=["POST"])
def add_patient():

    patient, error = validate_patient_data(request.form)

    if error:
        flash(error, "error")
        return redirect(url_for("index"))

    connection = get_connection()

    existing = connection.execute(
        "SELECT id FROM patients WHERE LOWER(email) = LOWER(?)",
        (patient["email"],)
    ).fetchone()

    if existing:
        connection.close()
        flash("A patient with this email already exists.", "error")
        return redirect(url_for("index"))

    remarks = generate_ai_remarks(
        patient["glucose"],
        patient["haemoglobin"],
        patient["cholesterol"]
    )

    connection.execute(
        """
        INSERT INTO patients
        (full_name, dob, email, glucose,
         haemoglobin, cholesterol, remarks)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            patient["full_name"],
            patient["dob"],
            patient["email"],
            patient["glucose"],
            patient["haemoglobin"],
            patient["cholesterol"],
            remarks
        )
    )

    connection.commit()
    connection.close()

    flash("Patient record added successfully.", "success")

    return redirect(url_for("index"))


# -----------------------------------
# Edit patient record
# -----------------------------------

@app.route("/edit/<int:patient_id>", methods=["GET", "POST"])
def edit_patient(patient_id):

    connection = get_connection()

    existing_patient = connection.execute(
        "SELECT * FROM patients WHERE id = ?",
        (patient_id,)
    ).fetchone()

    if not existing_patient:
        connection.close()
        flash("Patient record not found.", "error")
        return redirect(url_for("index"))

    if request.method == "POST":

        patient, error = validate_patient_data(request.form)

        if error:
            connection.close()
            flash(error, "error")
            return redirect(
                url_for("edit_patient", patient_id=patient_id)
            )

        duplicate = connection.execute(
            """
            SELECT id FROM patients
            WHERE LOWER(email) = LOWER(?) AND id != ?
            """,
            (patient["email"], patient_id)
        ).fetchone()

        if duplicate:
            connection.close()
            flash(
                "Another patient already uses this email.",
                "error"
            )
            return redirect(
                url_for("edit_patient", patient_id=patient_id)
            )

        remarks = generate_ai_remarks(
            patient["glucose"],
            patient["haemoglobin"],
            patient["cholesterol"]
        )

        connection.execute(
            """
            UPDATE patients
            SET full_name = ?, dob = ?, email = ?,
                glucose = ?, haemoglobin = ?,
                cholesterol = ?, remarks = ?
            WHERE id = ?
            """,
            (
                patient["full_name"],
                patient["dob"],
                patient["email"],
                patient["glucose"],
                patient["haemoglobin"],
                patient["cholesterol"],
                remarks,
                patient_id
            )
        )

        connection.commit()
        connection.close()

        flash("Patient record updated successfully.", "success")

        return redirect(url_for("index"))

    connection.close()

    return render_template(
        "edit.html",
        patient=existing_patient
    )


# -----------------------------------
# Delete patient record
# -----------------------------------

@app.route("/delete/<int:patient_id>", methods=["POST"])
def delete_patient(patient_id):

    connection = get_connection()

    connection.execute(
        "DELETE FROM patients WHERE id = ?",
        (patient_id,)
    )

    connection.commit()
    connection.close()

    flash("Patient record deleted successfully.", "success")

    return redirect(url_for("index"))


# -----------------------------------
# Start Flask application
# -----------------------------------

if __name__ == "__main__":
    app.run(debug=True)



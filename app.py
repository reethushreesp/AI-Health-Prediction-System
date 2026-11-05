
import os
import re
from datetime import datetime, date

from flask import Flask, render_template, request, redirect, url_for, flash
from dotenv import load_dotenv
from google import genai

from database import get_connection, create_table

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "local-development-key")

# Gemini AI configuration
api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key) if api_key else None


def validate_patient_data(form):
    """Validate patient details before saving."""

    errors = []

    full_name = form.get("full_name", "").strip()
    dob = form.get("dob", "").strip()
    email = form.get("email", "").strip()

    if not full_name:
        errors.append("Full name is required.")

    try:
        birth_date = datetime.strptime(dob, "%d/%m/%Y").date()

        if birth_date.strftime("%d/%m/%Y") != dob:
            errors.append("Date must be in DD/MM/YYYY format.")
        elif birth_date > date.today():
            errors.append("Date of birth cannot be in the future.")

    except ValueError:
        errors.append("Please enter a valid date in DD/MM/YYYY format.")

    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        errors.append("Please enter a valid email address.")

    try:
        glucose = float(form.get("glucose", ""))
        haemoglobin = float(form.get("haemoglobin", ""))
        cholesterol = float(form.get("cholesterol", ""))

        if not all(
            value > 0 and value < float("inf")
            for value in (glucose, haemoglobin, cholesterol)
        ):
            errors.append("Blood test values must be positive numbers.")

    except (ValueError, TypeError):
        errors.append("Blood test values must be numeric.")
        glucose = haemoglobin = cholesterol = 0

    if errors:
        return None, " ".join(errors)

    return {
        "full_name": full_name,
        "dob": dob,
        "email": email,
        "glucose": glucose,
        "haemoglobin": haemoglobin,
        "cholesterol": cholesterol
    }, None



def generate_ai_remarks(glucose, haemoglobin, cholesterol):
    """Request educational health information from Gemini AI."""

    if not client:
        return "AI remarks unavailable: Gemini API key is not configured."

    prompt = f"""
You are an educational health information assistant.

Review these blood test values:
Glucose: {glucose}
Haemoglobin: {haemoglobin}
Cholesterol: {cholesterol}

Provide a short, cautious explanation of possible health risk indicators
that may be worth discussing with a qualified healthcare professional.

Important:
- Do not diagnose a disease.
- Do not prescribe medicines or treatments.
- Do not assume units or testing conditions that were not provided.
- Explain that interpretation depends on units, reference ranges,
  age, medical history and whether the test was fasting.
- Recommend professional review of the results.
- If information is insufficient, clearly say so.

Return a short response in simple language.
"""

    try:
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=prompt
        )
        return response.text or "AI did not return any remarks."

    except Exception as error:
        print("Gemini API error:", error)
        return "AI remarks are temporarily unavailable. Please try again later."


@app.route("/")
def home():
    connection = get_connection()
    patients = connection.execute(
        "SELECT * FROM patients ORDER BY id DESC"
    ).fetchall()
    connection.close()

    return render_template("index.html", patients=patients)


@app.route("/add", methods=["POST"])
def add_patient():
    data, error = validate_patient_data(request.form)

    if error:
        flash(error)
        return redirect(url_for("home"))

    connection = get_connection()

    duplicate = connection.execute(
        "SELECT id FROM patients WHERE LOWER(email) = LOWER(?)",
        (data["email"],)
    ).fetchone()

    if duplicate:
        connection.close()
        flash("A patient with this email address already exists.")
        return redirect(url_for("home"))

    remarks = generate_ai_remarks(
        data["glucose"],
        data["haemoglobin"],
        data["cholesterol"]
    )

    connection.execute("""
        INSERT INTO patients
        (full_name, dob, email, glucose, haemoglobin, cholesterol, remarks)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        data["full_name"],
        data["dob"],
        data["email"],
        data["glucose"],
        data["haemoglobin"],
        data["cholesterol"],
        remarks
    ))

    connection.commit()
    connection.close()

    flash("Patient record added successfully.")
    return redirect(url_for("home"))


@app.route("/edit/<int:patient_id>", methods=["GET", "POST"])
def edit_patient(patient_id):
    connection = get_connection()

    patient = connection.execute(
        "SELECT * FROM patients WHERE id = ?", (patient_id,)
    ).fetchone()

    if not patient:
        connection.close()
        flash("Patient record not found.")
        return redirect(url_for("home"))

    if request.method == "POST":
        data, error = validate_patient_data(request.form)

        if error:
            connection.close()
            flash(error)
            return redirect(url_for("edit_patient", patient_id=patient_id))

        duplicate = connection.execute(
            """SELECT id FROM patients
               WHERE LOWER(email) = LOWER(?) AND id != ?""",
            (data["email"], patient_id)
        ).fetchone()

        if duplicate:
            connection.close()
            flash("Another patient already uses this email address.")
            return redirect(url_for("edit_patient", patient_id=patient_id))

        connection.execute("""
            UPDATE patients
            SET full_name = ?, dob = ?, email = ?,
                glucose = ?, haemoglobin = ?, cholesterol = ?
            WHERE id = ?
        """, (
            data["full_name"], data["dob"], data["email"],
            data["glucose"], data["haemoglobin"],
            data["cholesterol"], patient_id
        ))

        connection.commit()
        connection.close()

        flash("Patient record updated successfully.")
        return redirect(url_for("home"))

    connection.close()
    return render_template("edit.html", patient=patient)


@app.route("/delete/<int:patient_id>", methods=["POST"])
def delete_patient(patient_id):
    connection = get_connection()

    connection.execute(
        "DELETE FROM patients WHERE id = ?", (patient_id,)
    )

    connection.commit()
    connection.close()

    flash("Patient record deleted successfully.")
    return redirect(url_for("home"))


if __name__ == "__main__":
    create_table()
    app.run(debug=True)

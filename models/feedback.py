"""
models/feedback.py
-------------------
Intern Exit Feedback Form submitted by an intern at the end of their
internship: intern information, rating-scale sections covering learning
& development, supervisor/department support, work environment and
competencies developed, plus open feedback and an overall assessment.
One record per intern -- resubmitting updates the existing entry.
"""

from utils import now_pkt

from extensions import db


class Feedback(db.Model):
    """Intern Exit Feedback Form submission."""

    __tablename__ = "feedback"

    id = db.Column(db.Integer, primary_key=True)
    intern_id = db.Column(db.Integer, db.ForeignKey("interns.id"), unique=True, nullable=False)

    # ------------------------------------------------------------------
    # Rating scale used across Sections A, B and C (5 = Excellent ... 1 = Poor)
    # ------------------------------------------------------------------
    RATING_CHOICES = [1, 2, 3, 4, 5]
    RATING_LABELS = {5: "Excellent", 4: "Very Good", 3: "Good", 2: "Fair", 1: "Poor"}

    # Section D competency-improvement scale
    COMPETENCY_CHOICES = [
        "Significant Improvement",
        "Moderate",
        "Slight",
        "Not Improved",
    ]

    # Overall Assessment choices
    OVERALL_RATING_CHOICES = ["Excellent", "Very Good", "Good", "Fair", "Poor"]
    RECOMMEND_CHOICES = ["Definitely Yes", "Probably Yes", "Not Sure", "Probably No"]
    FUTURE_EMPLOYMENT_CHOICES = ["Yes", "Maybe", "No"]

    # ------------------------------------------------------------------
    # Section A -- Learning & Development (1-5 each)
    # ------------------------------------------------------------------
    a1_practical_learning = db.Column(db.Integer, nullable=False)
    a2_tasks_relevant = db.Column(db.Integer, nullable=False)
    a3_applied_knowledge = db.Column(db.Integer, nullable=False)
    a4_enhanced_skills = db.Column(db.Integer, nullable=False)
    a5_understanding_operations = db.Column(db.Integer, nullable=False)
    a6_career_development = db.Column(db.Integer, nullable=False)
    a7_learning_objectives_achieved = db.Column(db.Integer, nullable=False)

    # ------------------------------------------------------------------
    # Section B -- Supervisor & Department Support (1-5 each)
    # ------------------------------------------------------------------
    b1_supervisor_guidance = db.Column(db.Integer, nullable=False)
    b2_staff_cooperative = db.Column(db.Integer, nullable=False)
    b3_constructive_feedback = db.Column(db.Integer, nullable=False)

    # ------------------------------------------------------------------
    # Section C -- Work Environment (1-5 each)
    # ------------------------------------------------------------------
    c1_professional_inclusive = db.Column(db.Integer, nullable=False)
    c2_adequate_resources = db.Column(db.Integer, nullable=False)
    c3_felt_welcomed = db.Column(db.Integer, nullable=False)

    # ------------------------------------------------------------------
    # Section D -- Competencies Developed
    # (Significant Improvement / Moderate / Slight / Not Improved)
    # ------------------------------------------------------------------
    d1_communication_skills = db.Column(db.String(30), nullable=False)
    d2_teamwork_collaboration = db.Column(db.String(30), nullable=False)
    d3_problem_solving = db.Column(db.String(30), nullable=False)
    d4_professional_ethics = db.Column(db.String(30), nullable=False)
    d5_technical_knowledge = db.Column(db.String(30), nullable=False)

    # ------------------------------------------------------------------
    # Section E -- Open Feedback
    # ------------------------------------------------------------------
    valuable_learning = db.Column(db.Text, nullable=False)
    program_suggestions = db.Column(db.Text, nullable=True)

    # ------------------------------------------------------------------
    # Overall Assessment
    # ------------------------------------------------------------------
    overall_experience_rating = db.Column(db.String(20), nullable=False)
    recommend_program = db.Column(db.String(20), nullable=False)
    future_employment_interest = db.Column(db.String(10), nullable=False)

    submitted_at = db.Column(db.DateTime, default=now_pkt)
    updated_at = db.Column(
        db.DateTime,
        default=now_pkt,
        onupdate=now_pkt,
    )

    intern = db.relationship("Intern", backref=db.backref("feedback", uselist=False))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    RATING_FIELDS = [
        "a1_practical_learning",
        "a2_tasks_relevant",
        "a3_applied_knowledge",
        "a4_enhanced_skills",
        "a5_understanding_operations",
        "a6_career_development",
        "a7_learning_objectives_achieved",
        "b1_supervisor_guidance",
        "b2_staff_cooperative",
        "b3_constructive_feedback",
        "c1_professional_inclusive",
        "c2_adequate_resources",
        "c3_felt_welcomed",
    ]

    COMPETENCY_FIELDS = [
        "d1_communication_skills",
        "d2_teamwork_collaboration",
        "d3_problem_solving",
        "d4_professional_ethics",
        "d5_technical_knowledge",
    ]

    @property
    def average_rating(self):
        """Average of the 1-5 rating-scale answers (Sections A/B/C), for
        display purposes (e.g. star summary on the intern profile page)."""
        values = [getattr(self, f) for f in self.RATING_FIELDS if getattr(self, f) is not None]
        if not values:
            return 0
        return round(sum(values) / len(values), 1)

    def __repr__(self):
        return f"<Feedback Intern#{self.intern_id} overall={self.overall_experience_rating}>"

"""
models/evaluation.py
---------------------
Intern performance evaluations. A Project Manager can submit periodic
evaluations for interns assigned to their projects; HR can submit a
single Final Evaluation per intern. Both share the same six-criteria
scoring rubric so they can be compared/reported on uniformly.
"""

from datetime import datetime, timezone
from utils import now_pkt

from extensions import db


class Evaluation(db.Model):
    """A single evaluation record scored across six criteria (1-10 each)."""

    __tablename__ = "evaluations"

    id = db.Column(db.Integer, primary_key=True)

    intern_id = db.Column(db.Integer, db.ForeignKey("interns.id"), nullable=False)
    # The User (PM or HR) who submitted this evaluation.
    evaluated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    # Optional link to the project the evaluation relates to.
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=True)

    # 'Project Manager' (periodic, multiple allowed) or 'HR Final'
    # (one authoritative record per intern, enforced in routes/evaluation.py).
    evaluation_type = db.Column(db.String(20), nullable=False, default="Project Manager")

    # Scoring criteria, each rated 1-10.
    technical_skills = db.Column(db.Integer, nullable=False)
    communication = db.Column(db.Integer, nullable=False)
    discipline = db.Column(db.Integer, nullable=False)
    learning = db.Column(db.Integer, nullable=False)
    teamwork = db.Column(db.Integer, nullable=False)
    attendance_score = db.Column(db.Integer, nullable=False)

    remarks = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=now_pkt)
    updated_at = db.Column(
        db.DateTime,
        default=now_pkt,
        onupdate=now_pkt,
    )

    # Relationships
    intern = db.relationship("Intern", backref="evaluations")
    evaluated_by = db.relationship("User", backref="evaluations_given")
    project = db.relationship("Project", backref="evaluations")

    EVALUATION_TYPES = ["Project Manager", "HR Final"]
    CRITERIA = [
        ("technical_skills", "Technical Skills"),
        ("communication", "Communication"),
        ("discipline", "Discipline"),
        ("learning", "Learning"),
        ("teamwork", "Teamwork"),
        ("attendance_score", "Attendance"),
    ]
    MAX_PER_CRITERION = 10
    MAX_TOTAL = MAX_PER_CRITERION * len(CRITERIA)  # 60

    @property
    def total_score(self) -> int:
        """Sum of all six criteria, out of MAX_TOTAL (60). Calculated
        automatically rather than stored, so it can never drift out of
        sync with the individual criterion scores."""
        return (
            self.technical_skills
            + self.communication
            + self.discipline
            + self.learning
            + self.teamwork
            + self.attendance_score
        )

    @property
    def percentage(self) -> float:
        """Total score expressed as a percentage of MAX_TOTAL."""
        return round((self.total_score / self.MAX_TOTAL) * 100, 1)

    def __repr__(self):
        return f"<Evaluation Intern#{self.intern_id} {self.evaluation_type} {self.total_score}/60>"

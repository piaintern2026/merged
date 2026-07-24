"""
models/pm_evaluation.py
------------------------
Project Manager Evaluation Form (6-Week Internship Learning &
Performance Evaluation). This mirrors the official HR paper form
(week-by-week learning objectives/outcomes rated 1-5, plus a 10-item
competency scorecard rated 1-5) as a first-class, permanently-stored
record instead of the generic 6-criteria Evaluation model.

One record == one PM's evaluation of one intern on one project. A
Project Manager may only create/edit a record for an intern who is
currently assigned to one of their own projects (enforced in
routes/pm_evaluation.py). Once finalized, a record is locked for the
PM (HR/Admin retains edit rights for corrections).
"""

from utils import now_pkt, today_pkt

from extensions import db


class PMEvaluation(db.Model):
    """A single Project Manager Evaluation Form submission."""

    __tablename__ = "pm_evaluations"

    id = db.Column(db.Integer, primary_key=True)

    # Required links: Intern, Project, Project Manager, Evaluation Date.
    intern_id = db.Column(db.Integer, db.ForeignKey("interns.id"), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    project_manager_id = db.Column(
        db.Integer, db.ForeignKey("project_managers.id"), nullable=False
    )
    # The logged-in User who owns this record (kept alongside
    # project_manager_id so permission checks/audit trails can reuse the
    # same `evaluated_by_id` pattern as the existing Evaluation model).
    evaluated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    evaluation_date = db.Column(db.Date, nullable=False, default=today_pkt)

    # ------------------------------------------------------------------
    # Week 1-6: Activity / Learning Objective / Expected Outcome are
    # fixed (see WEEKS below) -- only the supervisor rating (1-5) is
    # actually captured per intern. Nullable so a PM can save progress
    # across the six weeks before finalizing.
    # ------------------------------------------------------------------
    week1_rating = db.Column(db.Integer, nullable=True)
    week2_rating = db.Column(db.Integer, nullable=True)
    week3_rating = db.Column(db.Integer, nullable=True)
    week4_rating = db.Column(db.Integer, nullable=True)
    week5_rating = db.Column(db.Integer, nullable=True)
    week6_rating = db.Column(db.Integer, nullable=True)

    # ------------------------------------------------------------------
    # Competency scorecard (10 items, 1-5 each).
    # ------------------------------------------------------------------
    attendance_punctuality = db.Column(db.Integer, nullable=True)
    professional_conduct = db.Column(db.Integer, nullable=True)
    communication_skills = db.Column(db.Integer, nullable=True)
    learning_ability = db.Column(db.Integer, nullable=True)
    initiative_ownership = db.Column(db.Integer, nullable=True)
    teamwork_collaboration = db.Column(db.Integer, nullable=True)
    problem_solving = db.Column(db.Integer, nullable=True)
    adaptability = db.Column(db.Integer, nullable=True)
    quality_of_work = db.Column(db.Integer, nullable=True)
    overall_performance = db.Column(db.Integer, nullable=True)

    # Free-text supervisor remarks + a recommendation checkbox group.
    remarks = db.Column(db.Text, nullable=True)
    recommendation = db.Column(db.String(50), nullable=True)

    is_finalized = db.Column(db.Boolean, default=False, nullable=False)
    finalized_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=now_pkt)
    updated_at = db.Column(db.DateTime, default=now_pkt, onupdate=now_pkt)

    # Relationships
    intern = db.relationship("Intern", backref="pm_evaluations")
    project = db.relationship("Project", backref="pm_evaluations")
    project_manager = db.relationship("ProjectManager", backref="pm_evaluations")
    evaluated_by = db.relationship("User", backref="pm_evaluations_given")

    # ------------------------------------------------------------------
    # Static form definition -- single source of truth shared by the
    # route validation logic and the Jinja templates, so the rendered
    # form always matches the official document exactly.
    # ------------------------------------------------------------------
    WEEKS = [
        {
            "number": 1,
            "field": "week1_rating",
            "activity": "Department Orientation & Familiarization",
            "objective": "Understand departmental structure, functions, policies and workflow",
            "outcome": "Demonstrates understanding of departmental operations and reporting lines",
        },
        {
            "number": 2,
            "field": "week2_rating",
            "activity": "On-the-Job Learning & Observation",
            "objective": "Observe day-to-day operational activities and procedures",
            "outcome": "Understands work processes and departmental responsibilities",
        },
        {
            "number": 3,
            "field": "week3_rating",
            "activity": "Task/Project Participation",
            "objective": "Apply academic knowledge to assigned tasks/projects",
            "outcome": "Performs assigned tasks with minimum supervision",
        },
        {
            "number": 4,
            "field": "week4_rating",
            "activity": "Practical Exposure & Cross-functional Coordination",
            "objective": "Develop communication, teamwork and problem-solving skills",
            "outcome": "Demonstrates collaboration and professional behavior",
        },
        {
            "number": 5,
            "field": "week5_rating",
            "activity": "Independent Assignment / Mini Project",
            "objective": "Enhance analytical thinking and decision-making",
            "outcome": "Successfully completes assigned project/activity",
        },
        {
            "number": 6,
            "field": "week6_rating",
            "activity": "Project Presentation & Review",
            "objective": "Present learning achievements and key outcomes",
            "outcome": "Demonstrates confidence, learning and practical understanding",
        },
    ]

    COMPETENCIES = [
        ("attendance_punctuality", "Attendance & Punctuality"),
        ("professional_conduct", "Professional Conduct & Discipline"),
        ("communication_skills", "Communication Skills"),
        ("learning_ability", "Learning Ability"),
        ("initiative_ownership", "Initiative & Ownership"),
        ("teamwork_collaboration", "Teamwork & Collaboration"),
        ("problem_solving", "Problem Solving"),
        ("adaptability", "Adaptability"),
        ("quality_of_work", "Quality of Work"),
        ("overall_performance", "Overall Performance"),
    ]

    RECOMMENDATIONS = [
        "Recommended for Extension",
        "Recommended for Permanent Hire",
        "Successfully Completed Internship",
        "Needs Improvement",
        "Not Recommended",
    ]

    MAX_PER_RATING = 5
    MAX_WEEK_TOTAL = MAX_PER_RATING * len(WEEKS)               # 30
    MAX_COMPETENCY_TOTAL = MAX_PER_RATING * len(COMPETENCIES)  # 50
    MAX_GRAND_TOTAL = MAX_WEEK_TOTAL + MAX_COMPETENCY_TOTAL    # 80

    # ------------------------------------------------------------------
    # Computed scoring -- always derived, never stored, so it can never
    # drift out of sync with the individual field ratings.
    # ------------------------------------------------------------------
    @property
    def week_scores(self):
        return [getattr(self, w["field"]) for w in self.WEEKS]

    @property
    def competency_scores(self):
        return [getattr(self, field) for field, _ in self.COMPETENCIES]

    @property
    def week_total(self) -> int:
        return sum(s for s in self.week_scores if s is not None)

    @property
    def competency_total(self) -> int:
        return sum(s for s in self.competency_scores if s is not None)

    @property
    def total_score(self) -> int:
        return self.week_total + self.competency_total

    @property
    def percentage(self) -> float:
        if self.MAX_GRAND_TOTAL == 0:
            return 0.0
        return round((self.total_score / self.MAX_GRAND_TOTAL) * 100, 1)

    @property
    def is_complete(self) -> bool:
        """True once every rating field has been scored -- required
        before a PM is allowed to finalize the form."""
        return all(s is not None for s in self.week_scores + self.competency_scores)

    def __repr__(self):
        return f"<PMEvaluation Intern#{self.intern_id} {self.total_score}/{self.MAX_GRAND_TOTAL}>"

"""
models package
---------------
Exposes all ORM models in one place so other modules can simply do:
    from models import User, Department, ProjectManager, Intern
"""

from models.user import User
from models.department import Department
from models.project_manager import ProjectManager
from models.intern import Intern
from models.project import Project
from models.attendance import Attendance
from models.leave import Leave
from models.submission import ProjectSubmission
from models.work_log import DailyWorkLog
from models.report import FinalReport
from models.feedback import Feedback
from models.notification import Notification
from models.evaluation import Evaluation
from models.pm_evaluation import PMEvaluation
from models.audit_log import AuditLog
from models.system_setting import SystemSetting
from models.rotation import InternRotation

__all__ = [
    "User",
    "Department",
    "ProjectManager",
    "Intern",
    "Project",
    "Attendance",
    "Leave",
    "ProjectSubmission",
    "DailyWorkLog",
    "FinalReport",
    "Feedback",
    "Notification",
    "Evaluation",
    "PMEvaluation",
    "AuditLog",
    "SystemSetting",
    "InternRotation",
]

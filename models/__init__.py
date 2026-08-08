"""
models package
---------------
Exposes all ORM models in one place so other modules can simply do:
    from models import User, Department, ProjectManager, Intern
"""

from models.user import User
from models.department import Department, SubDepartment
from models.project_manager import ProjectManager
from models.intern import Intern
from models.project import Project
from models.attendance import Attendance
from models.leave import Leave
from models.submission import ProjectSubmission
from models.report import FinalReport
from models.feedback import Feedback
from models.notification import Notification
from models.evaluation import Evaluation
from models.pm_evaluation import PMEvaluation
from models.audit_log import AuditLog
from models.system_setting import SystemSetting
from models.rotation import InternRotation
from models.pm_workspace import ProjectMilestone
from models.message import Message
from models.email_log import EmailLog
from models.announcement import Announcement

__all__ = [
    "User",
    "Department",
    "SubDepartment",
    "ProjectManager",
    "Intern",
    "Project",
    "Attendance",
    "Leave",
    "ProjectSubmission",
    "FinalReport",
    "Feedback",
    "Notification",
    "Evaluation",
    "PMEvaluation",
    "AuditLog",
    "SystemSetting",
    "InternRotation",
    "ProjectMilestone",
    "Message",
    "EmailLog",
    "Announcement",
]

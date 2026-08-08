"""
extensions.py
-------------
Instantiate Flask extensions here (without binding to an app yet) so that
models, routes and app.py can all import the same instances without
causing circular-import problems.
"""

from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_mail import Mail

# Single shared SQLAlchemy instance for the whole application.
db = SQLAlchemy()

# Single shared LoginManager instance.
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please log in to access this page."
login_manager.login_message_category = "warning"

# Single shared Flask-Mail instance used by services/email_service.py to
# send all outbound notification emails. Configuration (SMTP host,
# credentials, sender address, etc.) is loaded from environment
# variables in config.py, never hard-coded here.
mail = Mail()

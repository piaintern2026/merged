"""
services package
-----------------
Report-generation logic (PDF via ReportLab, Excel via OpenPyXL) lives
here, kept separate from routes/ so route handlers stay thin and the
generation logic is reusable/testable in isolation.
"""

"""Refusals an administrator can act on: plain sentences, each with the HTTP status it is shown
with."""

from __future__ import annotations


class EnrollmentError(Exception):
    status_code = 400

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code


class NotFound(EnrollmentError):
    status_code = 404


class Conflict(EnrollmentError):
    status_code = 409

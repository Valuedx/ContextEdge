"""ManageEngine ServiceDesk Plus data models."""

from datetime import datetime

from pydantic import BaseModel


class METicket(BaseModel):
    """Ticket data from ManageEngine SDP."""

    id: str
    ticket_number: str | None = None
    subject: str
    description: str | None = None
    short_description: str | None = None
    category: str | None = None
    subcategory: str | None = None
    priority: str | None = None
    impact: str | None = None
    urgency: str | None = None
    status: str | None = None
    group_name: str | None = None
    assignee_name: str | None = None
    resolution: str | None = None
    created_time: datetime | None = None
    closed_time: datetime | None = None
    raw_json: dict


class MEWorklog(BaseModel):
    """Worklog/note from ManageEngine SDP."""

    id: str
    ticket_id: str
    description: str
    technician_name: str | None = None
    created_time: datetime | None = None
    raw_json: dict

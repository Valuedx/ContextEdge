"""ManageEngine ServiceDesk Plus data models."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class METicket(BaseModel):
    """Ticket data from ManageEngine SDP."""
    id: str
    ticket_number: Optional[str] = None
    subject: str
    description: Optional[str] = None
    short_description: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    priority: Optional[str] = None
    impact: Optional[str] = None
    urgency: Optional[str] = None
    status: Optional[str] = None
    group_name: Optional[str] = None
    assignee_name: Optional[str] = None
    resolution: Optional[str] = None
    created_time: Optional[datetime] = None
    closed_time: Optional[datetime] = None
    raw_json: dict


class MEWorklog(BaseModel):
    """Worklog/note from ManageEngine SDP."""
    id: str
    ticket_id: str
    description: str
    technician_name: Optional[str] = None
    created_time: Optional[datetime] = None
    raw_json: dict


class MESolution(BaseModel):
    """Solution/KB article from ManageEngine SDP."""
    id: str
    title: str
    content: Optional[str] = None
    keywords: Optional[list[str]] = None
    category: Optional[str] = None
    created_time: Optional[datetime] = None
    updated_time: Optional[datetime] = None
    raw_json: dict

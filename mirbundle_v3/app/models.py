from __future__ import annotations

from pydantic import BaseModel, Field


class SettingsForm(BaseModel):
    host: str = Field(default='')
    username: str = Field(default='')
    password: str = Field(default='')
    mission_group_id: str = Field(default='')
    drive_endpoint_path: str = Field(default='')
    drive_http_method: str = Field(default='POST')
    drive_body_template: str = Field(default='{"linear": {{linear}}, "angular": {{angular}}}')
    camera_stream_url: str = Field(default='')
    camera_snapshot_url: str = Field(default='')


class QueueMissionForm(BaseModel):
    mission_id: str
    parameter_input_name: str = ''
    parameter_value: str = ''


class RegisterWriteForm(BaseModel):
    register_id: int
    value: str


class WorkflowCreateForm(BaseModel):
    name: str
    description: str = ''
    steps_json: str

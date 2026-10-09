"""Input and output validation for the Focus task breakdown."""
import json
from pydantic import BaseModel, Field, field_validator


class FocusRequest(BaseModel):
    code: str = Field(min_length=1, max_length=256)
    tasks: list[str] = Field(min_length=1, max_length=10)

    @field_validator('tasks')
    @classmethod
    def check_tasks(cls, tasks):
        tasks = [task.strip() for task in tasks]
        if any(not task or len(task) > 200 for task in tasks):
            raise ValueError('Each task must contain 1 to 200 characters')
        return tasks


def parse_breakdown(text: str, tasks: list[str]) -> dict:
    text = text.strip()
    if text.startswith('```'):
        text = '\n'.join(text.splitlines()[1:-1])
    result = json.loads(text)
    if not isinstance(result, dict) or result.get('task') not in tasks:
        raise ValueError('The selected task must come from the input')
    steps = result.get('steps')
    if not isinstance(steps, list) or not 3 <= len(steps) <= 5:
        raise ValueError('Expected 3 to 5 steps')
    if any(not isinstance(step, str) or not step.strip() or len(step) > 300 for step in steps):
        raise ValueError('Invalid step')
    return {'task': result['task'], 'steps': [step.strip() for step in steps]}

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def timestamp(value) -> str:
    if isinstance(value, (int, float)):
        parsed = datetime.fromtimestamp(value, timezone.utc)
    else:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Measurement(StrictModel):
    timestamp: str
    sensor: str = Field(min_length=1, max_length=128, pattern=r'^[\w.\- ]+$')
    value: float | None = None
    label: Literal[0, 1] | None = None

    @field_validator('timestamp', mode='before')
    @classmethod
    def check_time(cls, value):
        return timestamp(value)

    @field_validator('value')
    @classmethod
    def check_value(cls, value):
        if value is not None and not math.isfinite(value):
            raise ValueError('Значение должно быть конечным числом')
        return value


class DatasetCreate(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    source_id: str | None = None


class DatasetUpdate(StrictModel):
    name: str = Field(min_length=1, max_length=120)


class BulkRecords(StrictModel):
    records: list[Measurement] = Field(min_length=1, max_length=50000)
    source_offset: int | None = Field(default=None, ge=0)


class ImportRequest(StrictModel):
    source_id: str = 'iqds-demo'
    dataset_id: str | None = None
    all_batches: bool = True


class TrainRequest(StrictModel):
    dataset_id: str
    sensor: str
    name: str = Field(default='LSTM автоэнкодер', min_length=1, max_length=100)
    train_start: str = '2025-06-13'
    train_end: str = '2025-06-22'
    calibration_start: str = '2025-06-22'
    calibration_end: str = '2025-06-29'
    window: int = Field(default=144, ge=6, le=288)
    epochs: int = Field(default=120, ge=1, le=120)
    quantile: float = Field(default=0.99, ge=0.9, le=0.9999)
    seed: int = Field(default=42, ge=0, le=2**31 - 1)

    @field_validator('train_start', 'train_end', 'calibration_start', 'calibration_end', mode='before')
    @classmethod
    def check_time(cls, value):
        return timestamp(value)

    @model_validator(mode='after')
    def check_periods(self):
        if not self.train_start < self.train_end <= self.calibration_start < self.calibration_end:
            raise ValueError('Обучение должно предшествовать отдельному периоду настройки порога')
        return self


class PredictRequest(StrictModel):
    dataset_id: str
    start: str = '2025-07-20'
    end: str = '2025-09-09'

    @field_validator('start', 'end', mode='before')
    @classmethod
    def check_time(cls, value):
        return timestamp(value)

    @model_validator(mode='after')
    def check_period(self):
        if self.start >= self.end:
            raise ValueError('Начало периода должно предшествовать окончанию')
        return self


class AnalyzeRequest(PredictRequest):
    model_id: str

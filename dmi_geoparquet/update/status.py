"""Track per-block update progress in a JSON file served next to the dataset."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

STATUS_FILENAME = "_status.json"

BLOCK_STATES = ("waiting", "processing", "done", "empty", "error")


def utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string.

    Returns
    -------
    str
        Timestamp with second precision and a ``Z`` suffix.
    """

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_summary(path: Path) -> dict[str, Any]:
    """Summarize a Parquet file for the status page.

    Parameters
    ----------
    path
        Parquet file path.

    Returns
    -------
    dict
        ``rows`` and ``modified_at`` of the file.
    """

    modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    return {
        "rows": pq.read_metadata(path).num_rows,
        "modified_at": modified.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


class StatusTracker:
    """Hold update progress and persist it atomically after every change.

    Parameters
    ----------
    path
        JSON file to write. The leading underscore of the default name keeps
        it out of Parquet dataset discovery.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = {
            "updated_at": None,
            "cycle": {"state": "idle", "started_at": None, "finished_at": None},
            "next_run_at": None,
            "interval_hours": None,
            "parameters": [],
            "blocks": {},
            "partitions": {},
        }

    def write(self) -> None:
        """Write the status JSON atomically."""

        self.data["updated_at"] = utc_now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(f".{self.path.name}.tmp")
        tmp_path.write_text(json.dumps(self.data, indent=1, sort_keys=True))
        os.replace(tmp_path, self.path)

    def start_cycle(
        self,
        parameters: list[str],
        blocks: dict[str, dict[str, Any]],
        partitions: dict[str, dict[str, Any]],
    ) -> None:
        """Reset block and partition states at the beginning of a cycle.

        Parameters
        ----------
        parameters
            Parameter IDs, in display order.
        blocks
            Initial block entries keyed by :func:`block_key`.
        partitions
            Initial final-partition entries keyed by ``YYYY-MM`` period.
        """

        self.data["cycle"] = {"state": "running", "started_at": utc_now(), "finished_at": None}
        self.data["next_run_at"] = None
        self.data["parameters"] = parameters
        self.data["blocks"] = blocks
        self.data["partitions"] = partitions
        self.write()

    def update_block(self, key: str, **fields: Any) -> None:
        """Merge ``fields`` into one block entry and persist.

        Parameters
        ----------
        key
            Block key from :func:`block_key`.
        **fields
            Values such as ``state``, ``rows`` or ``error``.
        """

        self.data["blocks"][key].update(fields)
        self.write()

    def update_partition(self, period: str, **fields: Any) -> None:
        """Merge ``fields`` into one final-partition entry and persist.

        Parameters
        ----------
        period
            ``YYYY-MM`` month label.
        **fields
            Values such as ``state``, ``path``, ``rows`` or ``error``.
        """

        self.data["partitions"][period].update(fields)
        self.write()

    def finish_cycle(self, state: str) -> None:
        """Mark the cycle finished.

        Parameters
        ----------
        state
            ``ok`` or ``error``.
        """

        self.data["cycle"].update(state=state, finished_at=utc_now())
        self.write()

    def set_next_run(self, when: datetime, interval_hours: float) -> None:
        """Record when the next cycle starts.

        Parameters
        ----------
        when
            UTC start time of the next cycle.
        interval_hours
            Configured interval between cycles.
        """

        self.data["next_run_at"] = when.strftime("%Y-%m-%dT%H:%M:%SZ")
        self.data["interval_hours"] = interval_hours
        self.write()


def block_key(parameter_id: str, period: str) -> str:
    """Return the status key for a parameter and ``YYYY-MM`` period.

    Parameters
    ----------
    parameter_id
        DMI parameter ID.
    period
        Month label.

    Returns
    -------
    str
        ``"<parameter>/<period>"``.
    """

    return f"{parameter_id}/{period}"

"""Keep bounded read responses transient and retain only compact diagnostic metadata."""

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import SQLAlchemyError

from api_service.assistant.tool_execution_store import AssistantToolExecutionStore
from api_service.assistant.tools.definition import ToolResult
from backend_common.db import AssistantToolExecution, run_with_sqlite_lock_retry

MAX_READ_RESULT_BYTES = 180_000
READ_HISTORY_LIMIT = 1000
READ_HISTORY_DAYS = 7
logger = logging.getLogger(__name__)


def bounded_read_result(result: ToolResult) -> ToolResult:
    if len(json.dumps(result.as_dict()).encode()) > MAX_READ_RESULT_BYTES:
        return ToolResult.error("RESULT_TOO_LARGE: narrow the query", details={"code": "RESULT_TOO_LARGE"})
    return result


class ReadAuditStore(AssistantToolExecutionStore):
    @staticmethod
    def complete(db, execution, result):
        if db is None or execution is None:
            return
        bounded = bounded_read_result(result)
        encoded = json.dumps(bounded.as_dict()).encode()
        compact = ToolResult(
            content="Read failed; repeat the query for details." if bounded.is_error else "Read completed; result body is not retained.",
            is_error=bounded.is_error,
            details={"result_retained": False, "response_bytes": len(encoded), "response_sha256": hashlib.sha256(encoded).hexdigest()},
        )
        AssistantToolExecutionStore.complete(db, execution, compact, retain_arguments=False)
        client_id = execution.client_id

        def prune():
            db.rollback()
            db.connection(execution_options={"sqlite_begin_immediate": True})
            # Durable mutation identities and approvals never enter this query.
            history = db.query(AssistantToolExecution).filter(
                AssistantToolExecution.source == "mcp",
                AssistantToolExecution.client_id == client_id,
                AssistantToolExecution.risk == "read",
                AssistantToolExecution.status.in_(["succeeded", "failed", "ambiguous"]),
            )
            history.filter(AssistantToolExecution.created_at < datetime.now(UTC) - timedelta(days=READ_HISTORY_DAYS)).delete(
                synchronize_session=False
            )
            stale = (
                history.order_by(AssistantToolExecution.created_at.desc(), AssistantToolExecution.id.desc()).offset(READ_HISTORY_LIMIT).all()
            )
            for row in stale:
                db.delete(row)
            db.commit()

        try:
            run_with_sqlite_lock_retry(db, prune)
        except SQLAlchemyError:
            db.rollback()
            logger.warning("MCP read-history pruning deferred", exc_info=True)

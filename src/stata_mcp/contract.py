"""公开执行合同常量与 fail-closed 兼容校验。

v1alpha1 期间允许新增可选字段；既有字段不得改名、改类型或改变枚举语义。
读取方必须忽略未知字段，但缺少 required 字段、未知状态或不同 schema version
必须拒绝正式提升。冻结 v1 前由上层应用做版本协商，MCP 不自行 upcast。
"""
from __future__ import annotations


ENVELOPE_SCHEMA_VERSION = "stata-mcp.envelope/v1"
EXECUTION_RECEIPT_SCHEMA_VERSION = "stata.execution-receipt/v1alpha1"
SESSION_CONTROL_SCHEMA_VERSION = "stata.session-control/v1alpha1"
SESSION_OPEN_SCHEMA_VERSION = "stata.session-open/v1alpha1"
EXECUTOR_CAPABILITIES_SCHEMA_VERSION = "stata.executor-capabilities/v1alpha1"
EXECUTION_STATUSES = frozenset(
    {"succeeded", "command_failed", "timed_out", "crashed", "start_failed"}
)
RAW_OUTPUT_STATUSES = frozenset({"complete", "truncated", "unavailable"})
STRUCTURED_RESULT_STATUSES = frozenset(
    {"complete", "not_applicable", "parse_failed", "partial"}
)
EXECUTION_RECEIPT_REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "executor_instance_id",
        "session_id",
        "session_generation",
        "exec_seq",
        "execution_status",
        "rc",
        "raw_output_status",
        "structured_result_status",
        "command_hash",
        "data_signature",
        "session_reset",
        "runtime_environment",
        "supervision_proof",
    }
)


def validate_execution_receipt(receipt: object) -> list[str]:
    """返回兼容性错误；空列表表示可被 v1alpha1 consumer 安全读取。"""
    if not isinstance(receipt, dict):
        return ["receipt must be an object"]
    errors = []
    missing = sorted(EXECUTION_RECEIPT_REQUIRED_FIELDS - receipt.keys())
    if missing:
        errors.append("missing required fields: " + ", ".join(missing))
    if receipt.get("schema_version") != EXECUTION_RECEIPT_SCHEMA_VERSION:
        errors.append("unsupported schema_version")
    if receipt.get("execution_status") not in EXECUTION_STATUSES:
        errors.append("unknown execution_status")
    if receipt.get("raw_output_status") not in RAW_OUTPUT_STATUSES:
        errors.append("unknown raw_output_status")
    if receipt.get("structured_result_status") not in STRUCTURED_RESULT_STATUSES:
        errors.append("unknown structured_result_status")
    if not receipt.get("executor_instance_id"):
        errors.append("executor_instance_id is required")
    if not receipt.get("session_id"):
        errors.append("session_id is required")
    generation = receipt.get("session_generation")
    if not isinstance(generation, int) or generation < 1:
        errors.append("session_generation must be a positive integer")
    if not isinstance(receipt.get("rc"), int):
        errors.append("rc must be an integer")
    if not isinstance(receipt.get("runtime_environment"), dict):
        errors.append("runtime_environment must be an object")
    if not isinstance(receipt.get("supervision_proof"), dict):
        errors.append("supervision_proof must be an object")
    return errors

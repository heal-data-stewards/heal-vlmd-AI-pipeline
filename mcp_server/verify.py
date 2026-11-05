"""Manual Phase-1 verification script — not part of the test suite, run by
hand against a running `python -m mcp_server.server` to sanity-check every
v1 tool end to end, especially the validate_vlmd regression check (a
schema-invalid file must actually come back valid=False, not be silently
treated as success like the old chat_pipeline.py code path did).

Usage:
    python -m mcp_server.server &
    python mcp_server/verify.py
"""
import asyncio
import json
import os
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

REPO_ROOT = Path(__file__).resolve().parent.parent
URL = os.environ.get("MCP_URL", "http://localhost:8090/mcp")


async def main() -> None:
    async with streamable_http_client(URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print(f"Tools registered ({len(names)}): {names}")
            expected = {
                "get_file_info", "detect_format", "list_excel_sheets",
                "extract_excel_sheet", "extract_pdf_table", "convert_to_vlmd",
                "validate_vlmd", "fixup_flagged_fields", "merge_vlmd",
                "save_format_mapping",
            }
            missing = expected - set(names)
            assert not missing, f"Missing tools: {missing}"

            resources = await session.list_resources()
            print(f"Resources: {[r.uri for r in resources.resources]}")

            def result_json(call_tool_result):
                text = call_tool_result.content[0].text
                return json.loads(text)

            # ── get_file_info ──────────────────────────────────────────
            clean_csv = REPO_ROOT / "demo" / "demo_dd.csv"
            r = await session.call_tool("get_file_info", {"file_path": str(clean_csv)})
            info = result_json(r)
            print("get_file_info:", info)
            assert info["success"] and info["size_bytes"] > 0

            # ── detect_format ──────────────────────────────────────────
            r = await session.call_tool("detect_format", {"file_path": str(clean_csv)})
            detected = result_json(r)
            print("detect_format:", {k: detected[k] for k in ("format_name", "confidence", "row_count", "column_count")})
            assert detected["success"]
            assert detected["row_count"] > 0 and detected["columns"]

            format_yaml = str(REPO_ROOT / "formats" / "redcap.yaml")

            # ── convert_to_vlmd + validate_vlmd: CLEAN file ────────────
            work = REPO_ROOT / "work" / "_mcp_verify"
            work.mkdir(parents=True, exist_ok=True)
            out_fields = str(work / "clean_fields.json")
            out_lint = str(work / "clean_lint.json")
            r = await session.call_tool("convert_to_vlmd", {
                "input_path": str(clean_csv), "format_yaml": format_yaml,
                "output_fields": out_fields, "output_lint": out_lint,
            })
            conv = result_json(r)
            print("convert_to_vlmd (clean):", conv)
            assert conv["success"]

            report_path = str(work / "clean_report.json")
            r = await session.call_tool("validate_vlmd", {"fields_path": out_fields, "report_path": report_path})
            val = result_json(r)
            print("validate_vlmd (clean):", {k: val[k] for k in ("valid", "error_count")})
            assert val["valid"] is True, f"Expected clean file to validate, got: {val}"

            # ── convert_to_vlmd + validate_vlmd: FLAGGED file (regression
            #    test for the actual bug being fixed) ────────────────────
            flagged_csv = REPO_ROOT / "demo" / "demo_dd_flagged.csv"
            out_fields2 = str(work / "flagged_fields.json")
            out_lint2 = str(work / "flagged_lint.json")
            r = await session.call_tool("convert_to_vlmd", {
                "input_path": str(flagged_csv), "format_yaml": format_yaml,
                "output_fields": out_fields2, "output_lint": out_lint2,
            })
            conv2 = result_json(r)
            print("convert_to_vlmd (flagged):", conv2)
            assert conv2["success"]
            assert conv2["flagged_field_count"] > 0, "Expected demo_dd_flagged.csv to flag at least one field"

            # An unrecognized Field Type is a CONTENT flag (surfaced via
            # flagged_field_count above, resolved by fixup_flagged_fields),
            # NOT a schema error — so validate_vlmd correctly still reports
            # this file as schema-valid. Not a bug in validate_vlmd.

            # The actual failure-path regression test: a deliberately
            # schema-invalid fields file must come back valid=False with
            # real errors. This is the case chat_pipeline.py used to read
            # and then silently discard.
            bad_fields = work / "schema_invalid_fields.json"
            bad_fields.write_text(json.dumps([
                {"name": "bad_one", "description": "x", "type": "not_a_real_vlmd_type"},
            ]), encoding="utf-8")
            report_path2 = str(work / "schema_invalid_report.json")
            r = await session.call_tool("validate_vlmd", {"fields_path": str(bad_fields), "report_path": report_path2})
            val2 = result_json(r)
            print("validate_vlmd (schema-invalid):", {k: val2[k] for k in ("valid", "error_count")})
            assert val2["valid"] is False, f"Expected schema-invalid fixture to fail validation, got: {val2}"
            assert val2["error_count"] > 0 and val2["errors"]

            # ── skill resource ──────────────────────────────────────────
            r = await session.read_resource("skill://vlmd-conversion")
            skill_text = r.contents[0].text
            print(f"skill resource length: {len(skill_text)} chars")
            assert "Answer whatever the user actually asks" in skill_text

            print("\nAll Phase 1 checks passed.")


if __name__ == "__main__":
    asyncio.run(main())

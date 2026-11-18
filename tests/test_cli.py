"""End-to-end tests for the patchloom CLI."""

from __future__ import annotations

import io
import json
from pathlib import Path

from patchloom.__main__ import main


def test_cli_diff_apply_and_get(tmp_path: Path) -> None:
    src_file = tmp_path / "src.json"
    dst_file = tmp_path / "dst.json"
    patch_file = tmp_path / "patch.json"

    src_file.write_text('{"app": "api", "port": 8000}', encoding="utf-8")
    dst_file.write_text('{"app": "api", "port": 9000, "tls": true}', encoding="utf-8")

    out = io.StringIO()
    err = io.StringIO()
    code = main(["diff", str(src_file), str(dst_file)], stdout=out, stderr=err)
    assert code == 0
    patch_file.write_text(out.getvalue(), encoding="utf-8")

    out_apply = io.StringIO()
    code_apply = main(["apply", str(src_file), str(patch_file)], stdout=out_apply, stderr=err)
    assert code_apply == 0
    assert json.loads(out_apply.getvalue()) == {"app": "api", "port": 9000, "tls": True}

    out_get = io.StringIO()
    code_get = main(["get", str(dst_file), "/port"], stdout=out_get, stderr=err)
    assert code_get == 0
    assert json.loads(out_get.getvalue()) == 9000


def test_cli_reversible_merge_patch_and_merge3_conflict(tmp_path: Path) -> None:
    b_file = tmp_path / "base.json"
    o_file = tmp_path / "ours.json"
    t_file = tmp_path / "theirs.json"

    b_file.write_text('{"x": 1}', encoding="utf-8")
    o_file.write_text('{"x": 2}', encoding="utf-8")
    t_file.write_text('{"x": 3}', encoding="utf-8")

    out = io.StringIO()
    err = io.StringIO()
    assert main(["diff", "--reversible", str(b_file), str(o_file)], stdout=out, stderr=err) == 0
    rev_payload = json.loads(out.getvalue())
    assert "forward" in rev_payload and "reverse" in rev_payload

    # Conflict with default policy=raise exits 1
    out_m3 = io.StringIO()
    err_m3 = io.StringIO()
    assert main(["merge3", str(b_file), str(o_file), str(t_file)], stdout=out_m3, stderr=err_m3) == 1
    assert "conflict" in err_m3.getvalue().lower()

    # Policy=theirs succeeds with exit code 0
    out_ok = io.StringIO()
    assert (
        main(
            ["merge3", "--policy", "theirs", str(b_file), str(o_file), str(t_file)],
            stdout=out_ok,
            stderr=err_m3,
        )
        == 0
    )
    assert json.loads(out_ok.getvalue()) == {"x": 3}
